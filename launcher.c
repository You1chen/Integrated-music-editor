/* launcher.c — 双击 exe 自动运行 main.py（不弹黑框）
 *
 * 编译（任选一种）：
 *   gcc    : gcc -o lrc-maker.exe launcher.c -mwindows
 *   MSVC   : cl launcher.c /link /SUBSYSTEM:WINDOWS
 *
 * 把 lrc-maker.exe 放在和 main.py 同一个目录即可。
 */

#include <windows.h>
#include <stdio.h>

int WINAPI WinMain(
    HINSTANCE hInstance,
    HINSTANCE hPrevInstance,
    LPSTR     lpCmdLine,
    int       nShowCmd
) {
    /* 获取 exe 所在目录 */
    char exe_dir[MAX_PATH];
    GetModuleFileName(NULL, exe_dir, MAX_PATH);
    char *last = strrchr(exe_dir, '\\');
    if (last) *last = '\0';

    /* cmd /c uv run python "D:\...\main.py" */
    char cmd[1024];
    snprintf(cmd, sizeof(cmd), "cmd /c uv run python \"%s\\main.py\"", exe_dir);

    STARTUPINFO si = { sizeof(si) };
    PROCESS_INFORMATION pi;
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;

    if (!CreateProcess(
        NULL, cmd, NULL, NULL, FALSE,
        CREATE_NO_WINDOW, NULL, exe_dir, &si, &pi
    )) {
        MessageBox(NULL,
            "启动失败，请确认已安装 uv。\n在终端执行 uv run python main.py 试试？",
            "LRC Maker", MB_ICONERROR);
        return 1;
    }

    /* 等 Python 退出后再结束（如果不等，exe 瞬间退出，任务栏图标一闪就没） */
    WaitForSingleObject(pi.hProcess, INFINITE);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    return 0;
}
