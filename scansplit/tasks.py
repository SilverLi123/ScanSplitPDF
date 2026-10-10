"""后台任务系统（v1.9）：单工作线程 + Queue + root.after 回传。

设计约束（对应 v1.9 任务书）：
- Tkinter 只允许主线程更新：工作线程只往队列放消息，主线程轮询消费；
- 单工作线程：同一时刻只跑一个任务（稳定优先，不做线程池）；
- 线程归属：PageRenderer 等渲染资源由工作函数在worker线程内自建自毁，
  绝不跨线程共享主线程的渲染器；
- task_id 唯一且绑定文档对象：完成回调只作用于捕获的文档对象，
  用户切换文档不影响结果归属；文档被移除则结果丢弃并提示；
- 取消原子性：可变任务（检测/命名/复核）启动前由调用方留存快照，
  取消/异常时由调用方恢复，绝不留半更新状态；
- 导出取消保留已验证成功的文件（exporter 既有行为），临时文件必清理。

消息协议（queue.Queue 元组）：
  ("progress", done, total, phase)   进度（worker → 主线程）
  ("log", msg)                       日志（worker → 主线程）
  ("done", result)                   成功完成
  ("canceled",)                      用户取消（work 正常返回）
  ("error", str)                     work 抛异常
"""

from __future__ import annotations

import queue
import threading
import traceback


class TaskManager:
    """App 持有。所有回调（on_done/on_error）在 Tk 主线程执行。"""

    def __init__(self, root, log_fn):
        self.root = root
        self._log = log_fn  # callable(str)，主线程
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._seq = 0
        self.task_id: str | None = None  # 当前/最近一次任务标识
        self.last_error: str | None = None

    # -- 对外 API -----------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, kind: str, work, on_done, *, title: str = "") -> bool:
        """启动后台任务。work(progress_cb, log_cb, cancel_check) -> result。

        on_done(result, canceled, error) 在主线程回调；error 非 None 表示异常。
        已有任务在跑时返回 False（调用方提示"请等待当前任务完成"）。
        """
        if self.busy:
            return False
        self._seq += 1
        self.task_id = f"{kind}#{self._seq}"
        self.last_error = None
        self._cancel.clear()
        q = self._queue
        cancel = self._cancel

        def progress_cb(done, total, phase=""):
            q.put(("progress", done, total, phase))

        def log_cb(msg):
            q.put(("log", msg))

        def cancel_check() -> bool:
            return cancel.is_set()

        def _worker():
            try:
                result = work(progress_cb, log_cb, cancel_check)
                if cancel.is_set():
                    q.put(("canceled",))
                else:
                    q.put(("done", result))
            except Exception:
                q.put(("error", traceback.format_exc(limit=8)))

        self._thread = threading.Thread(
            target=_worker, name=f"scansplit-{kind}", daemon=True
        )
        self._thread.start()
        self._poll(on_done, title)
        return True

    def cancel(self):
        """请求取消当前任务（非阻塞，worker 在下一个检查点停止）。"""
        self._cancel.set()

    def cancel_and_join(self, timeout: float = 3.0) -> bool:
        """关窗用：请求取消并等待 worker 退出。返回是否已退出。"""
        self._cancel.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    # -- 内部 -----------------------------------------------------------------

    def _poll(self, on_done, title: str):
        """主线程轮询：消费队列消息；任务结束帧到达后收尾。"""
        finished = False
        try:
            while True:
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, done, total, phase = msg
                    if title:
                        self._set_progress(title, done, total, phase)
                elif kind == "log":
                    self._log(msg[1])
                elif kind in ("done", "canceled", "error"):
                    finished = True
                    result, canceled, error = None, False, None
                    if kind == "done":
                        result = msg[1]
                    elif kind == "canceled":
                        canceled = True
                    else:
                        error = msg[1]
                        self.last_error = error
                    if on_done is not None:
                        on_done(result, canceled, error)
        except queue.Empty:
            pass
        if not finished:
            self.root.after(40, lambda: self._poll(on_done, title))

    # 由 App 注入（避免本模块依赖 ui_widgets 造成循环导入）
    progress_hook: object = None  # callable(title, done, total, phase)

    def _set_progress(self, title, done, total, phase):
        if self.progress_hook is not None:
            self.progress_hook(title, done, total, phase)
