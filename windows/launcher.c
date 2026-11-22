/* Copyright (C) 2026 PiaoSu Contributors
 * SPDX-License-Identifier: MIT
 *
 * 双击启动内置 Python 运行时的桌面引导程序：不请求管理员权限，不依赖外部 VC++ 运行库。
 */
#ifndef UNICODE
#define UNICODE
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#ifdef _MSC_VER
#include <stdio.h>
#else
int __cdecl _snwprintf(wchar_t *buffer, size_t count, const wchar_t *format, ...);
#endif

static void dirname_of(wchar_t *path) {
    wchar_t *cursor = path;
    wchar_t *slash = NULL;
    while (*cursor != L'\0') {
        if (*cursor == L'\\') {
            slash = cursor;
        }
        cursor++;
    }
    if (slash != NULL) {
        *slash = L'\0';
    }
}

int WINAPI WinMain(HINSTANCE instance, HINSTANCE previous, LPSTR command, int show) {
    wchar_t exe_path[1024];
    wchar_t pythonw[1200];
    wchar_t cmdline[1600];
    DWORD length;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    (void)instance;
    (void)previous;
    (void)command;
    (void)show;

    length = GetModuleFileNameW(NULL, exe_path, 1024);
    if (length == 0 || length >= 1023) {
        MessageBoxW(NULL, L"无法确定程序所在目录。", L"票溯 PiaoSu", MB_ICONERROR);
        return 1;
    }
    dirname_of(exe_path);
    if (_snwprintf(pythonw, 1200, L"%s\\runtime\\pythonw.exe", exe_path) < 0 ||
        _snwprintf(cmdline, 1600, L"\"%s\" -m piaosu gui", pythonw) < 0) {
        MessageBoxW(NULL, L"程序所在路径过长，请将文件夹移至较短路径后重试。", L"票溯 PiaoSu", MB_ICONERROR);
        return 1;
    }
    if (GetFileAttributesW(pythonw) == INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,
                    L"找不到内置运行环境（runtime\\pythonw.exe）。请保持解压目录完整，不要单独移动 piaosu.exe。",
                    L"票溯 PiaoSu", MB_ICONERROR);
        return 1;
    }

    SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1");
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", L"1");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");

    ZeroMemory(&startup, sizeof startup);
    startup.cb = sizeof startup;
    ZeroMemory(&process, sizeof process);
    if (!CreateProcessW(pythonw, cmdline, NULL, NULL, FALSE, 0, NULL, exe_path, &startup, &process)) {
        MessageBoxW(NULL, L"无法启动票溯主界面，请检查运行目录完整性。", L"票溯 PiaoSu", MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return 0;
}
