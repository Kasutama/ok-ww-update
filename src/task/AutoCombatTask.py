import threading
import time
import weakref

from ok import TriggerTask, Logger
from pynput import keyboard
from src.char.CharFactory import char_names
from src.scene.WWScene import WWScene
from src.task.BaseCombatTask import BaseCombatTask, NotInCombatException, CharDeadException

logger = Logger.get_logger(__name__)


class CombatToggleHotkey:
    """F5 全局热键：运行时快速切换 自动战斗 / 手动战斗。

    框架 ok-script 没有给业务任务暴露全局热键通道（StartCard 的
    RegisterHotKey 只服务开始/暂停，且 vk_map 仅 F9-F12），因此用 pynput
    GlobalHotKeys 在业务侧注册单个 F5，不改动框架。监听线程只负责把切换
    动作投递到任务自己的 Handler 线程执行（与界面勾选框走同一个
    enable()/disable() 公开入口），避免在监听线程里直接做设备操作；任务
    实例用 WeakSet 持有，executor 销毁后不会残留引用或阻止回收。
    """

    HOTKEY = '<f5>'
    DEBOUNCE_SECONDS = 0.3

    def __init__(self, listener_factory=None):
        self._tasks = weakref.WeakSet()
        self._lock = threading.Lock()
        self._listener = None
        self._next_allowed = 0.0
        # 测试可注入假监听工厂；默认走 pynput 全局热键
        self._listener_factory = listener_factory

    def register(self, task):
        """注册战斗任务实例并惰性启动全局 F5 监听。"""
        with self._lock:
            self._tasks.add(task)
            if self._listener is None:
                self._listener = self._start_listener()
        return task

    def _start_listener(self):
        try:
            if self._listener_factory is not None:
                listener = self._listener_factory(self._on_hotkey)
            else:
                listener = keyboard.GlobalHotKeys({self.HOTKEY: self._on_hotkey})
            listener.daemon = True
            listener.start()
            logger.info('combat toggle hotkey registered: F5')
            return listener
        except Exception as e:
            # 无桌面会话 / F5 被其他程序占用等情况不应阻断任务系统启动
            logger.error(f'failed to register F5 combat toggle hotkey: {e}')
            return None

    def _on_hotkey(self):
        now = time.monotonic()
        with self._lock:
            if now < self._next_allowed:
                logger.debug('F5 combat toggle ignored: debounce')
                return
            self._next_allowed = now + self.DEBOUNCE_SECONDS
            tasks = list(self._tasks)
        for task in tasks:
            try:
                # Handler 线程串行执行切换，避免连按 F5 与设备操作竞争
                task.handler.post(lambda t=task: self.toggle(t))
            except Exception as e:
                logger.error(f'failed to post F5 combat toggle: {e}')

    def toggle(self, task):
        """在任务 Handler 线程执行：切换启用状态并通知用户。"""
        try:
            if getattr(task, 'enabled', False):
                task.disable()
                message = 'F5：已切换为手动战斗'
            else:
                task.enable()
                message = 'F5：已切换为自动战斗'
            logger.info(message)
            task.notification(message, title='Auto Combat', tray=True)
        except Exception as e:
            logger.error(f'F5 combat toggle failed: {e}')


# 进程级单例：executor 重建时复用同一个全局监听
combat_toggle_hotkey = CombatToggleHotkey()


class AutoCombatTask(BaseCombatTask, TriggerTask):
    owns_switch_healer_config = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {'_enabled': True}
        self.trigger_interval = 0.1
        self.name = "⚔️ Auto Combat"
        self.description = "Enable auto combat in Abyss, Game World etc. Press F5 to quickly toggle auto/manual combat at runtime."
        self.last_is_click = False
        self.default_config.update({
            'Auto Target': True,
            'Use Liberation': True,
            'Check Levitator': True,
            'Switch to Healer before and after Combat': True,
        })
        self.config_description = {
            'Auto Target': 'Turn off to enable auto combat only when manually target enemy using middle click',
            'Use Liberation': 'Do not use Liberation in Open World to Save Time',
            'Check Levitator': 'Toggle the levitator and verify if the character is floating',
            'Switch to Healer before and after Combat': 'Better Chance to Keep Character Alive',
        }
        self.op_index = 0
        self.char_features_warmed_up = False

    def on_create(self):
        super().on_create()
        combat_toggle_hotkey.register(self)

    def warm_up_char_features(self):
        if self.char_features_warmed_up:
            return
        try:
            for char_name in char_names:
                self.get_feature_by_name(char_name)
        except Exception as e:
            logger.warning(f'warm_up_char_features failed: {e}')
            return
        self.char_features_warmed_up = True
        logger.info(f'warm_up_char_features loaded {len(char_names)} character templates')

    def run(self):
        self.warm_up_char_features()
        ret = False
        if not self.scene.in_team(self.in_team_and_world):
            return ret
        self.use_liberation = self.config.get('Use Liberation')
        if not self.use_liberation and not self.in_world():  # 仅大世界生效
            self.use_liberation = True
        combat_start = time.time()
        switched_to_healer = False
        while self.in_combat():
            ret = True
            try:
                if not switched_to_healer:
                    self.switch_healer()
                    switched_to_healer = True
                self.get_current_char().perform()
            except CharDeadException:
                self.log_error(f'Characters dead', notify=True)
                break
            except NotInCombatException as e:
                logger.info(f'auto_combat_task_out_of_combat {int(time.time() - combat_start)} {e}')
                break
        if ret:
            self.combat_end()
            self.switch_healer()
        return ret

    def realm_perform(self):
        if not self.last_is_click:
            if self.op_index % 10 == 0:
                self.send_key_and_wait_animation('4', self.in_illusive_realm, enter_animation_wait=0.2)
            else:
                self.click()
        else:
            if self.available('liberation'):
                self.send_key_and_wait_animation(self.get_liberation_key(), self.in_illusive_realm)
            elif self.available('echo'):
                self.send_key(self.get_echo_key())
            elif self.available('resonance'):
                self.send_key(self.get_resonance_key())
            elif self.is_con_full() and self.in_team()[0]:
                self.send_key_and_wait_animation('2', self.in_illusive_realm)
        self.last_is_click = not self.last_is_click
        self.op_index += 1
        self.sleep(0.02)


from ok import run_task
from config import config

if __name__ == "__main__":
    run_task(config, task=AutoCombatTask, debug=True)
