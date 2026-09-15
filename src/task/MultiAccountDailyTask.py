import re


from ok import Box
from src.task.DailyTask import DailyTask
from src.task.WWOneTimeTask import WWOneTimeTask
from src.task.BaseCombatTask import BaseCombatTask
from src.task.BaseWWTask import LOGIN_TEXTS
from src.task.AutoLoginTask import AutoLoginTask
from src.task.MouseResetTask import MouseResetTask

account_pattern = re.compile(r'\*\*\*\*')

# 进入游戏/开始游戏 按钮文案（切换账号后登录页按钮可能变为此类文案）
ENTER_GAME_TEXTS = ["开始游戏", "開始遊戲", re.compile('进入游戏|進入遊戲|Start Game|Enter Game', re.IGNORECASE)]

# OCR 容易混淆的字符映射，用于账号后缀的模糊比对
_CONFUSABLE_CHARS = str.maketrans({
    '0': 'o', 'o': 'o',
    '1': 'l', 'l': 'l', 'i': 'l',
    '5': 's', 's': 's',
    '2': 'z', 'z': 'z',
    '8': 'b', 'b': 'b',
})


def normalize_account_name(account):
    if not account:
        return account
    return account.lower().replace('0', 'o').replace('.con', '.com')


def fuzzy_account_key(account):
    """生成容错比对键：忽略大小写、混淆字符差异，用于 OCR 误读场景。"""
    if not account:
        return account
    text = account.lower().replace('.con', '.com')
    return text.translate(_CONFUSABLE_CHARS)


class MultiAccountDailyTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "👥 Multi Account Daily Task"
        self.description = "Automatically switch accounts and run Daily Task for each account"
        self.add_exit_after_config()
        self.done_set = set()
        self.all_accounts = set()
        self.support_schedule_task = True

    def _mark_done(self, account):
        normalized = normalize_account_name(account)
        if normalized:
            self.done_set.add(normalized)

    def _is_done(self, account):
        # done_set 存标准归一化键；先精确查，查不到再做容错比对（应对 OCR 误读）
        normalized = normalize_account_name(account)
        if normalized in self.done_set:
            return True
        fuzzy_key = fuzzy_account_key(account)
        return any(fuzzy_account_key(done) == fuzzy_key for done in self.done_set)

    def _same_account(self, left, right):
        if not left or not right:
            return False
        # 先精确归一化比对，再做容错比对（应对 OCR 误读 0/o、1/l 等）
        return normalize_account_name(left) == normalize_account_name(right) or \
            fuzzy_account_key(left) == fuzzy_account_key(right)

    def run(self):
        WWOneTimeTask.run(self)
        self.done_set.clear()
        self.all_accounts.clear()

        self.run_task_by_class(DailyTask)
        self.ensure_main(time_out=100)
        self._switch_to_login()
        detected = self._detect_current_account_from_login()
        self._mark_done(detected)

        self.info_set('Completed', self.done_set)

        while next_account := self._select_and_login_account():
            self.info_set('Completed', self.done_set)
            self.run_task_by_class(DailyTask)
            self._mark_done(next_account)
            self.ensure_main(time_out=100)
            self._switch_to_login()

    def _click_center_offset(self, offset_x, offset_y, after_sleep=0.5):
        h, w = self.frame.shape[:2]
        rel_x = 0.5 + offset_x / w
        rel_y = 0.5 + offset_y / h
        self.click_relative(rel_x, rel_y, after_sleep=after_sleep)

    def _switch_to_login(self):
        self.log_info(self.tr('Switching back to login screen'))
        self.send_key('esc', after_sleep=1.5)
        self.wait_feature('esc_setting')
        self.click_relative(0.04, 0.96, after_sleep=1)
        self.click_confirm(timeout=10)
        self.find_account_drop_down()
        self.log_info(self.tr('Back at login screen'))

    def _detect_current_account_from_login(self):
        texts = self.ocr(match=account_pattern)
        if texts:
            self.log_info(self.tr('Current account: {account}').format(account=texts[0]))
            return texts[0].name
        return None

    def _click_account_in_list(self):
        accounts = self.ocr(match=account_pattern)
        next_account = None
        # self.screenshot('_click_account_in_list')
        for account in accounts:
            self.all_accounts.add(normalize_account_name(account.name))
            self.info_set('All Accounts', self.all_accounts)
            if next_account is None and not self._is_done(account.name):
                next_account = account.name
                self.click(account, after_sleep=2)
        self.log_info(self.tr('Click next account: {account}').format(account=next_account))
        return next_account

    def _select_and_login_account(self):
        current_account = None
        mouse_reset_task = self.executor.get_task_by_class(MouseResetTask)
        mouse_reset_was_enabled = mouse_reset_task.enabled if mouse_reset_task else False
        if mouse_reset_was_enabled:
            mouse_reset_task.disable()
        # 切号期间禁用 AutoLoginTask，避免其并发点击（切换账号确认键/开始游戏）
        # 打断本任务的点击时序
        auto_login_task = self.executor.get_task_by_class(AutoLoginTask)
        auto_login_was_enabled = auto_login_task.enabled if auto_login_task else False
        if auto_login_was_enabled:
            auto_login_task.disable()
        try:
            max_retries = 5
            for attempt in range(1, max_retries + 1):
                # self.ensure_in_front()
                # self.update_capture({
                #     'windows': {
                #         'interaction': 'Pynput',
                #         'capture_method': 'ForegroundBitBlt',
                #     }
                # })
                self.sleep(1)
                drop_down = self.find_account_drop_down()
                if drop_down:
                    self.click(drop_down, after_sleep=2)
                if self.do_find_account_drop_down():
                    self.log_error('click drop down no effect')
                    self.screenshot('multi')
                    continue
                account = self.wait_until(
                    lambda: self._click_account_in_list(),
                    time_out=10, raise_if_not_found=True
                )
                # 等待下拉列表收起后再校验当前账号，避免把列表项误判为当前账号
                self._wait_dropdown_closed()
                current_account = self._detect_current_account_from_login()
                self.log_info(self.tr('Selected account: {selected}, displayed account: {displayed}').format(
                    selected=account, displayed=current_account))
                if self._same_account(account, current_account):
                    self.log_info(self.tr('Confirmed selected account: {account}').format(account=account))
                    break
                if attempt < max_retries:
                    self.log_info(self.tr('Account display does not match, retrying ({attempt}/{max_retries})').format(
                        attempt=attempt, max_retries=max_retries))
                else:
                    self.log_error(self.tr(
                        'Account selection failed after {max_retries} retries; {account} is still not displayed. Continuing login attempt'
                    ).format(max_retries=max_retries, account=account))
                    raise Exception(self.tr('Failed to switch account'))
            self.sleep(4)
            texts = self.ocr()
            login_btn = self.find_boxes(texts, boundary=self.box_of_screen(0.3, 0.3, 0.7, 0.8),
                                        match=LOGIN_TEXTS)
            if not login_btn:
                # 切换账号后按钮文案可能是 进入游戏/开始游戏，不匹配 LOGIN_TEXTS，
                # 在全屏范围内查找后再点击
                login_btn = self.find_boxes(texts, match=ENTER_GAME_TEXTS)
            if login_btn:
                self.click(login_btn, after_sleep=3)
            else:
                self.click_relative(0.5, 0.568, hcenter=True, vcenter=True, after_sleep=3)
            self.logged_in = False
            # self.update_capture({
            #     'windows': {
            #         'interaction': 'PostMessage',
            #         'capture_method': ['WGC', 'BitBlt_RenderFull'],
            #     }
            # })
            self.ensure_main(time_out=180)
            self.log_info(self.tr('Login successful'))
            return current_account
        finally:
            if mouse_reset_was_enabled:
                mouse_reset_task.enable()
            if auto_login_was_enabled:
                auto_login_task.enable()

    def _wait_dropdown_closed(self, time_out=10):
        """等待账号下拉列表收起：收起后屏幕上只剩 1 个掩码账号文本。"""
        return self.wait_until(
            lambda: len(self.ocr(match=account_pattern)) <= 1,
            time_out=time_out, raise_if_not_found=False
        )

    def find_account_drop_down(self):
        return self.wait_until(self.do_find_account_drop_down, time_out=60, settle_time=2, raise_if_not_found=True)

    def do_find_account_drop_down(self) -> Box | None:
        texts = self.ocr()
        account_boxes = self.find_boxes(texts, account_pattern)
        login_boxes = self.find_boxes(texts, LOGIN_TEXTS)
        if len(account_boxes) == 1 and login_boxes:
            return account_boxes[0]
        return None


from ok import run_task
from config import config

if __name__ == "__main__":
    run_task(config, task=MultiAccountDailyTask, debug=True)
