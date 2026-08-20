/*
 * anyvmtd -- the anyvm remote-exec channel for ReactOS guests.
 *
 * ReactOS ships no remote-access server at all: base/applications/network
 * has a telnet CLIENT and nothing that listens, and the rapps database
 * offers only PuTTY and WinSCP (also clients). plan9-builder could simply
 * tell 9front's own ip/telnetd to listen; this builder has to supply the
 * server, so here it is.
 *
 * What it does: listen on TCP 23, and hand every accepted connection to a
 * fresh cmd.exe with its standard handles on pipes, pumping bytes both
 * ways until either side closes. That is exactly the shape base-builder's
 * telnet_exec() drives -- it opens one connection per call, writes command
 * lines terminated with CRLF, and reads the transcript back.
 *
 * There is NO authentication, deliberately, matching plan9-builder's
 * no-auth telnetd: the port is reachable only through the QEMU slirp
 * hostfwd, which anyvm binds to 127.0.0.1 on the host. The guest is not
 * on a routable network.
 *
 * Runs either as a Win32 service (the normal case -- started at boot with
 * no logon required) or, with -c, as a plain console program for hand
 * debugging over VNC. Anything worth knowing goes to C:\anyvm\anyvmtd.log,
 * because a failed CI build has no console to look at.
 *
 * Build (see hooks/host_beforeBuild.sh):
 *   i686-w64-mingw32-gcc -O2 -static -o anyvmtd.exe anyvmtd.c -lws2_32
 */

#include <winsock2.h>
#include <windows.h>
#include <winsvc.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

#define SERVICE_NAME    "anyvmtd"
#define DEFAULT_PORT    23
#define LOG_DIR         "C:\\anyvm"
#define LOG_PATH        "C:\\anyvm\\anyvmtd.log"
#define IO_BUF          4096

/* Telnet protocol bytes -- RFC 854 sec "Command Structure". */
#define TN_SE           240
#define TN_SB           250
#define TN_WILL         251
#define TN_WONT         252
#define TN_DO           253
#define TN_DONT         254
#define TN_IAC          255

static volatile int g_stop = 0;
static SERVICE_STATUS_HANDLE g_status_handle = NULL;
static SERVICE_STATUS g_status;
static int g_port = DEFAULT_PORT;

/* ------------------------------------------------------------------ */
/* logging                                                            */
/* ------------------------------------------------------------------ */

static void logmsg(const char *fmt, ...)
{
    va_list ap;
    FILE *f;
    SYSTEMTIME st;

    CreateDirectoryA(LOG_DIR, NULL);
    f = fopen(LOG_PATH, "a");
    if (f == NULL)
        return;
    GetLocalTime(&st);
    fprintf(f, "%04u-%02u-%02u %02u:%02u:%02u ",
            st.wYear, st.wMonth, st.wDay,
            st.wHour, st.wMinute, st.wSecond);
    va_start(ap, fmt);
    vfprintf(f, fmt, ap);
    va_end(ap);
    fputc('\n', f);
    fclose(f);
}

/* ------------------------------------------------------------------ */
/* telnet option negotiation                                          */
/* ------------------------------------------------------------------ */

/*
 * Strip telnet negotiation out of `in`, refusing every option, and copy
 * the remaining plain bytes to `out` (which must be at least `len`
 * bytes). Returns the number of plain bytes produced.
 *
 * We never initiate negotiation. A client that offers an option gets a
 * flat refusal: IAC DO x -> IAC WONT x, IAC WILL x -> IAC DONT x. This
 * mirrors what base-builder's own _telnet_eat_iac() does on the host
 * side, so the two ends never wait on each other.
 *
 * Subnegotiation (IAC SB ... IAC SE) is skipped wholesale. A doubled
 * IAC IAC is the escape for a literal 255 byte.
 */
static int iac_filter(SOCKET s, const unsigned char *in, int len,
                      unsigned char *out)
{
    int i = 0;
    int n = 0;
    unsigned char reply[3];

    while (i < len) {
        if (in[i] != TN_IAC) {
            out[n++] = in[i++];
            continue;
        }
        if (i + 1 >= len)
            break;              /* truncated command; drop it */
        if (in[i + 1] == TN_IAC) {
            out[n++] = TN_IAC;  /* escaped literal 0xFF */
            i += 2;
            continue;
        }
        if (in[i + 1] == TN_SB) {
            i += 2;
            while (i + 1 < len && !(in[i] == TN_IAC && in[i + 1] == TN_SE))
                i++;
            i += 2;
            continue;
        }
        if (in[i + 1] == TN_DO || in[i + 1] == TN_DONT ||
            in[i + 1] == TN_WILL || in[i + 1] == TN_WONT) {
            if (i + 2 >= len)
                break;
            reply[0] = TN_IAC;
            reply[1] = (in[i + 1] == TN_DO || in[i + 1] == TN_DONT)
                       ? TN_WONT : TN_DONT;
            reply[2] = in[i + 2];
            send(s, (const char *)reply, 3, 0);
            i += 3;
            continue;
        }
        i += 2;                 /* any other two-byte command */
    }
    return n;
}

/* ------------------------------------------------------------------ */
/* connection handling                                                */
/* ------------------------------------------------------------------ */

/*
 * A pipe write is not all-or-nothing. WriteFile can report success having
 * taken FEWER bytes than asked for -- which is what happens here whenever
 * the child stops draining: tar writing a multi-megabyte member to disk
 * fills the pipe buffer, the write comes back short, and every byte past
 * `wrote` is gone. The stream then resumes mid-member, so every following
 * tar header lands misaligned and the child dies on "invalid tar magic".
 *
 * Measured on a live guest before this loop existed: pushing four members
 * totalling 2.45 MB failed every time while the same 2.45 MB as ONE member
 * went through, and the payload's CONTENT made no difference (random bytes
 * of the same sizes failed identically) -- the signature of lost bytes, not
 * of anything the data says. The socket direction already loops in
 * send_all(); this is its missing counterpart.
 */
static int write_all(HANDLE h, const unsigned char *buf, int len)
{
    int done = 0;
    DWORD n;

    while (done < len) {
        if (!WriteFile(h, buf + done, (DWORD)(len - done), &n, NULL))
            return -1;
        if (n == 0)
            return -1;
        done += (int)n;
    }
    return 0;
}

static int send_all(SOCKET s, const char *buf, int len)
{
    int sent = 0;
    int rc;

    while (sent < len) {
        rc = send(s, buf + sent, len - sent, 0);
        if (rc == SOCKET_ERROR || rc == 0)
            return -1;
        sent += rc;
    }
    return 0;
}

/*
 * Child output -> socket, with telnet framing kept honest: a literal 0xFF
 * data byte MUST go out as the doubled IAC IAC escape (RFC 854), exactly
 * the inverse of what iac_filter() undoes on the inbound side. Without
 * this, binary child output (the tar-sync pull streams an archive through
 * here) hits the host-side IAC parser and gets eaten or misread as
 * negotiation. Text output never contains 0xFF, so nothing else changes.
 */
static int send_iac_escaped(SOCKET s, const unsigned char *buf, int len)
{
    static unsigned char esc[IO_BUF * 2];
    int i;
    int n = 0;

    for (i = 0; i < len; i++) {
        if (buf[i] == TN_IAC)
            esc[n++] = TN_IAC;
        esc[n++] = buf[i];
    }
    return send_all(s, (const char *)esc, n);
}

/*
 * Run one cmd.exe against one socket. Both pipes are polled rather than
 * threaded: PeekNamedPipe for the child's output, select() for the
 * socket. Anonymous pipes cannot be used with select/WaitForSingleObject,
 * which is why the child side is a poll with a short socket timeout
 * providing the pacing.
 */
static void serve_conn(SOCKET s)
{
    SECURITY_ATTRIBUTES sa;
    HANDLE in_rd = NULL, in_wr = NULL;
    HANDLE out_rd = NULL, out_wr = NULL;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char cmdline[MAX_PATH];
    unsigned char raw[IO_BUF];
    unsigned char clean[IO_BUF];
    char outbuf[IO_BUF];
    DWORD avail, want, got;
    struct timeval tv;
    fd_set rfds;
    int rc, n;
    int stdin_eof = 0;

    sa.nLength = sizeof(sa);
    sa.lpSecurityDescriptor = NULL;
    sa.bInheritHandle = TRUE;

    if (!CreatePipe(&in_rd, &in_wr, &sa, 0) ||
        !CreatePipe(&out_rd, &out_wr, &sa, 0)) {
        logmsg("CreatePipe failed: %lu", (unsigned long)GetLastError());
        goto cleanup;
    }
    /* Keep the parent's ends out of the child. */
    SetHandleInformation(in_wr, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(out_rd, HANDLE_FLAG_INHERIT, 0);

    if (GetEnvironmentVariableA("COMSPEC", cmdline, sizeof(cmdline)) == 0)
        strcpy(cmdline, "cmd.exe");

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    si.hStdInput = in_rd;
    si.hStdOutput = out_wr;
    si.hStdError = out_wr;
    ZeroMemory(&pi, sizeof(pi));

    if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE,
                        CREATE_NO_WINDOW, NULL, "C:\\", &si, &pi)) {
        logmsg("CreateProcess(%s) failed: %lu",
               cmdline, (unsigned long)GetLastError());
        goto cleanup;
    }
    /* The child owns these now. */
    CloseHandle(in_rd);  in_rd = NULL;
    CloseHandle(out_wr); out_wr = NULL;

    for (;;) {
        /* child -> socket, drained greedily so output never lags */
        avail = 0;
        if (PeekNamedPipe(out_rd, NULL, 0, NULL, &avail, NULL) && avail > 0) {
            want = (avail > sizeof(outbuf)) ? sizeof(outbuf) : avail;
            if (!ReadFile(out_rd, outbuf, want, &got, NULL) || got == 0)
                break;
            if (send_iac_escaped(s, (const unsigned char *)outbuf,
                                 (int)got) != 0)
                break;
            continue;
        }

        /* socket -> child */
        if (!stdin_eof) {
            FD_ZERO(&rfds);
            FD_SET(s, &rfds);
            tv.tv_sec = 0;
            tv.tv_usec = 50000; /* 50 ms */
            n = select(0, &rfds, NULL, NULL, &tv);
            if (n == SOCKET_ERROR)
                break;
            if (n > 0) {
                rc = recv(s, (char *)raw, sizeof(raw), 0);
                if (rc < 0)
                    break;
                if (rc == 0) {
                    /*
                     * The peer half-closed its send side (tar sync does
                     * this after streaming an archive: it is the child's
                     * stdin EOF). Close the pipe so the child sees EOF,
                     * but KEEP pumping its remaining output -- the whole
                     * point of the half-close is that the client still
                     * wants the completion marker the child prints on the
                     * way out. A full close by the peer lands here too;
                     * the next send() then fails and ends the loop.
                     */
                    stdin_eof = 1;
                    if (in_wr != NULL) {
                        CloseHandle(in_wr);
                        in_wr = NULL;
                    }
                    continue;
                }
                rc = iac_filter(s, raw, rc, clean);
                if (rc > 0 && write_all(in_wr, clean, rc) != 0)
                    break;
            }
        } else {
            Sleep(50);          /* pace the output polling after EOF */
        }

        if (g_stop)
            break;

        /* Child gone and its pipe drained -> the session is over. */
        if (WaitForSingleObject(pi.hProcess, 0) == WAIT_OBJECT_0) {
            avail = 0;
            if (!PeekNamedPipe(out_rd, NULL, 0, NULL, &avail, NULL) ||
                avail == 0)
                break;
        }
    }

    /* Closing the child's stdin is the polite EOF; kill whatever ignores it. */
    if (in_wr != NULL) {
        CloseHandle(in_wr);
        in_wr = NULL;
    }
    if (WaitForSingleObject(pi.hProcess, 2000) != WAIT_OBJECT_0)
        TerminateProcess(pi.hProcess, 1);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);

cleanup:
    if (in_rd != NULL)  CloseHandle(in_rd);
    if (in_wr != NULL)  CloseHandle(in_wr);
    if (out_rd != NULL) CloseHandle(out_rd);
    if (out_wr != NULL) CloseHandle(out_wr);
}

/* ------------------------------------------------------------------ */
/* accept loop                                                        */
/* ------------------------------------------------------------------ */

static int run_server(void)
{
    WSADATA wsa;
    SOCKET lst, cli;
    struct sockaddr_in addr;
    struct timeval tv;
    fd_set rfds;
    int on = 1;
    int n;

    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        logmsg("WSAStartup failed: %d", WSAGetLastError());
        return 1;
    }
    lst = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (lst == INVALID_SOCKET) {
        logmsg("socket failed: %d", WSAGetLastError());
        WSACleanup();
        return 1;
    }
    setsockopt(lst, SOL_SOCKET, SO_REUSEADDR, (const char *)&on, sizeof(on));

    ZeroMemory(&addr, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons((unsigned short)g_port);

    if (bind(lst, (struct sockaddr *)&addr, sizeof(addr)) == SOCKET_ERROR) {
        /*
         * Almost always "another anyvmtd already has port 23" -- the
         * install script registers both a service and a Run key on
         * purpose, so whichever loses the race is expected to exit
         * quietly rather than look like a failure.
         */
        logmsg("bind to port %d failed: %d (already running?)",
               g_port, WSAGetLastError());
        closesocket(lst);
        WSACleanup();
        return 0;
    }
    if (listen(lst, 4) == SOCKET_ERROR) {
        logmsg("listen failed: %d", WSAGetLastError());
        closesocket(lst);
        WSACleanup();
        return 1;
    }
    logmsg("anyvmtd listening on port %d", g_port);

    while (!g_stop) {
        FD_ZERO(&rfds);
        FD_SET(lst, &rfds);
        tv.tv_sec = 1;
        tv.tv_usec = 0;
        n = select(0, &rfds, NULL, NULL, &tv);
        if (n == SOCKET_ERROR)
            break;
        if (n == 0)
            continue;           /* timeout -- recheck g_stop */
        cli = accept(lst, NULL, NULL);
        if (cli == INVALID_SOCKET)
            continue;
        serve_conn(cli);
        closesocket(cli);
    }

    logmsg("anyvmtd stopping");
    closesocket(lst);
    WSACleanup();
    return 0;
}

/* ------------------------------------------------------------------ */
/* service plumbing                                                   */
/* ------------------------------------------------------------------ */

static void report_status(DWORD state, DWORD wait_hint)
{
    if (g_status_handle == NULL)
        return;
    g_status.dwCurrentState = state;
    g_status.dwWaitHint = wait_hint;
    SetServiceStatus(g_status_handle, &g_status);
}

static void WINAPI service_ctrl(DWORD code)
{
    if (code == SERVICE_CONTROL_STOP || code == SERVICE_CONTROL_SHUTDOWN) {
        g_stop = 1;
        report_status(SERVICE_STOP_PENDING, 5000);
    }
}

static void WINAPI service_main(DWORD argc, LPSTR *argv)
{
    (void)argc;
    (void)argv;

    ZeroMemory(&g_status, sizeof(g_status));
    g_status.dwServiceType = SERVICE_WIN32_OWN_PROCESS;
    g_status.dwControlsAccepted = SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN;

    g_status_handle = RegisterServiceCtrlHandlerA(SERVICE_NAME, service_ctrl);
    if (g_status_handle == NULL) {
        logmsg("RegisterServiceCtrlHandler failed: %lu",
               (unsigned long)GetLastError());
        return;
    }
    report_status(SERVICE_RUNNING, 0);
    run_server();
    report_status(SERVICE_STOPPED, 0);
}

int main(int argc, char **argv)
{
    SERVICE_TABLE_ENTRYA table[2];
    int console = 0;
    int i;

    for (i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-c") == 0)
            console = 1;
        else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc)
            g_port = atoi(argv[++i]);
    }

    if (console)
        return run_server();

    table[0].lpServiceName = SERVICE_NAME;
    table[0].lpServiceProc = service_main;
    table[1].lpServiceName = NULL;
    table[1].lpServiceProc = NULL;

    /*
     * Started from the Run key (or by hand) rather than by the SCM, this
     * call fails with ERROR_FAILED_SERVICE_CONTROLLER_CONNECT. That is a
     * supported way to run: fall through to the plain server so the
     * belt-and-braces Run-key registration still brings the port up if
     * the service registration did not take.
     */
    if (!StartServiceCtrlDispatcherA(table)) {
        logmsg("not started by the SCM (%lu); running standalone",
               (unsigned long)GetLastError());
        return run_server();
    }
    return 0;
}
