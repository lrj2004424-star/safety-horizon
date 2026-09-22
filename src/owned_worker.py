"""Wait for job ownership before running a child / 确认进程归属后启动服务。"""
import runpy
import sys
from pathlib import Path

if __name__ == '__main__':
    if sys.stdin.buffer.readline() != b'GO\n':
        raise SystemExit(2)
    sys.argv = sys.argv[1:]
    target = Path(sys.argv[0]).resolve()
    if target.parent != Path(__file__).resolve().parent or target.suffix != '.py':
        raise SystemExit('Worker must be a project Python module')
    runpy.run_path(str(target), run_name='__main__')
