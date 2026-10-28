import subprocess, shlex, os


def run_capture(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"})
        return p.returncode, p.stdout, p.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return 127, "", str(e)


def run_stream(cmd, on_line, cancel=None):
    on_line("$ " + shlex.join(cmd))
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                             stdin=subprocess.DEVNULL)
    except FileNotFoundError as e:
        on_line(f"Ошибка: {e}")
        return 127
    for line in p.stdout:
        on_line(line.rstrip("\n"))
        if cancel and cancel():
            p.terminate()
    return p.wait()
