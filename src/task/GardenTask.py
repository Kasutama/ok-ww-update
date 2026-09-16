import re
import time


from ok import Logger, run_task
from config import config
from src.Labels import Labels
from src.task.BaseWWTask import BaseWWTask
from src.task.WWOneTimeTask import WWOneTimeTask

logger = Logger.get_logger(__name__)


class GardenTask(WWOneTimeTask, BaseWWTask):
    GARDEN_TARGET_POINTS = re.compile('6000')
    # 乐园活动主界面顶部速度档位：×1.0 / ×3.0 / ×5.0 / MAX，点击按钮循环切换
    GARDEN_SPEED_MAX = re.compile(r'M[\s.·]*A[\s.·]*X', re.IGNORECASE)
    # 必须带 ×/x 前缀：按钮右侧紧邻资源栏（晶石数量等裸数字），裸 "5" 绝不能当成 ×5 档
    GARDEN_SPEED_LOW = re.compile(r'[xX×]\s*[135](?:\.0)?')
    GARDEN_SPEED_LEVELS = {'1': 0, '3': 1, '5': 2}
    GARDEN_SPEED_MAX_LEVEL = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🎡 自动周常乐园"
        self.description = "Detect and click garden actions until the task is stopped."
        # 速度档位检测节流：两次检测至少间隔秒数
        self._last_speed_check = 0.0
        self._speed_max_logged = False
        # 待验证的点击：(点击时档位等级, 点击时刻)
        self._speed_pending = None
        # 连续点击无效的次数
        self._speed_stuck = 0
        # 冷却截止时刻；某些界面（如非战斗的经营主界面）按钮可能不响应，冷却后随界面变化重试
        self._speed_cooldown_until = 0.0
        self.garden_features = [
            label.value for label in Labels
            if label.value.startswith("garden_")
        ]
        self.garden_priority_features = [
            "garden_get_skip",
            "garden_not_interested_confirm",
        ]

    def run(self):
        WWOneTimeTask.run(self)
        self.ensure_main()
        self.open_garden_weekly_page()
        if self.is_weekly_garden_completed():
            self.log_info('乐园任务完成, 已达到上限', notify=True)
            return
        self.click(0.246, 0.486, after_sleep=1)
        while True:
            self.sleep(0.1)
            # 乐园活动主界面/战斗内顶部都有速度档位胶囊（×1.0/×3.0/×5.0/MAX），
            # 检测到非 MAX 档位时点一下，循环到 MAX 为止
            self.ensure_garden_speed_max()
            target = self.find_best_garden_feature()
            self.sleep(0.2)
            if target:
                self.info_set("current task", target.name)
                if target.name == 'garden_get_skip':
                    self.sleep(1)
                    self.log_info(f"click garden_get_confirm")
                    if gold := self.find_one('garden_get_gold', horizontal_variance=0.9):
                        self.click(gold, after_sleep=1)
                    elif purple := self.find_one('garden_get_purple', horizontal_variance=0.9):
                        self.click(purple, after_sleep=1)
                    else:
                        self.click(0.5, 0.2, after_sleep=1)
                    confirm = self.safe_box_by_name('garden_get_confirm_gray')
                    if confirm:
                        self.click(confirm, after_sleep=1)
                    else:
                        self.log_debug('garden_get_confirm_gray not found, skip confirm click')
                    continue
                elif target.name == 'garden_not_interested':
                    not_interested = self.find_feature('garden_not_interested', vertical_variance=0.4)
                    if not not_interested:
                        # find_best_garden_feature 检测到后界面已切换，二次检测为空，跳过本轮
                        self.log_debug('garden_not_interested vanished before click, skip')
                        continue
                    self.click(not_interested[-1], after_sleep=1)
                    not_interested_confirm = self.safe_box_by_name('garden_not_interested_confirm')
                    if not_interested_confirm:
                        self.click(not_interested_confirm, after_sleep=1)
                    continue
                elif target.name == 'garden_start_game':
                    # At Garden Entrance, choose blessing1
                    self._choose_first_blessing()
                self.log_info(f"click {target.name} {target.confidence:.3f}")
                self.click(target, after_sleep=1)
            else:
                garden_restart = self.find_one('a_garden_restart')
                garden_back = self.find_one('a_garden_back')
                if garden_restart and garden_back:
                    # 避免因点击太快，导致[挑战失败]页面中点击[返回主页]失败
                    self.sleep(2)
                    texts = self.ocr(0.373, 0.346, 0.859, 0.615)
                    self.log_info('garden end {}'.format(texts))
                    if self.is_garden_done(texts):
                        self.click(garden_back, after_sleep=1)
                        if self.wait_feature('garden_start_game', settle_time=1, time_out=5):
                            self.back(after_sleep=1)
                        if self.wait_book('gray_book_quest', time_out=30):
                            self.click(0.927, 0.893, after_sleep=2)
                            self.click(0.927, 0.893, after_sleep=1)
                        break
                    else:
                        self.click(garden_restart, after_sleep=1)
                self.sleep(0.2)
        self.log_info('乐园任务完成, 已达到上限', notify=True)

    def open_garden_weekly_page(self):
        self.openF2Book('gray_book_quest')
        self.sleep(1)
        self.click(0.343, 0.129, after_sleep=1)
        self.click(0.927, 0.893, after_sleep=3)
        self.click(0.927, 0.893, after_sleep=2)

    def is_weekly_garden_completed(self):
        current = self.ocr(0.102, 0.793, 0.284, 0.956, match=self.GARDEN_TARGET_POINTS)
        self.log_info(f"Garden current: {current}")
        return bool(current)

    def is_garden_done(self, texts):
        text = " ".join(str(getattr(box, "name", box)) for box in texts)
        return text.count(self.GARDEN_TARGET_POINTS.pattern) != 1

    def safe_box_by_name(self, name):
        """get_box_by_name 的不抛异常版本，找不到返回 None。"""
        try:
            return self.get_box_by_name(name)
        except Exception as e:
            self.log_debug(f'box {name} not found: {e}')
            return None

    def _speed_level_of(self, name):
        """把 OCR 文本映射成档位等级（×1=0/×3=1/×5=2/MAX=3），无法识别返回 None。"""
        name = name.strip()
        if self.GARDEN_SPEED_MAX.fullmatch(name):
            return self.GARDEN_SPEED_MAX_LEVEL
        if self.GARDEN_SPEED_LOW.fullmatch(name):
            digit = re.search(r'[135]', name).group(0)
            return self.GARDEN_SPEED_LEVELS[digit]
        return None

    def ensure_garden_speed_max(self):
        """检测顶部居中的速度胶囊按钮（×1.0/×3.0/×5.0/MAX），逐步点到 MAX。

        安全策略：
        - OCR 只在紧贴按钮的小区域内识别，且档位文字必须带 ×/x 前缀，
          避免把右侧资源栏的裸数字（如晶石数量）误判成档位而误点；
        - 每次点击后下一轮验证档位是否真的前进；连续 3 次无效说明当前界面
          按钮不响应（如非战斗的经营主界面），冷却 12 秒再试，进入战斗后自动补上；
        - 读不到明确档位文字（弹窗/加载/非乐园界面）绝不点击；
        - 调档失败不是任务错误，只记日志不弹通知，不中断周常流程。
        """
        now = time.monotonic()
        if now - self._last_speed_check < 1.5:
            return
        self._last_speed_check = now
        # 实测 1920x1040 按钮位于 (1219,41)-(1343,82)，中心约 (1281,61)；
        # 区域右缘收到 0.705，避开右侧紧邻的晶石资源栏
        speed_box = self.box_of_screen(0.625, 0.025, 0.705, 0.095, hcenter=True, vcenter=True)
        try:
            texts = self.ocr(box=speed_box)
        except Exception as e:
            self.log_debug(f'garden speed ocr failed: {e}')
            return
        # 取检测框中心最靠近区域水平中线的档位候选
        level = None
        target_box = None
        target_name = None
        region_cx = speed_box.x + speed_box.width / 2
        best_distance = None
        for box in texts:
            name = str(getattr(box, 'name', box)).strip()
            parsed_level = self._speed_level_of(name)
            if parsed_level is None:
                continue
            box_cx = box.x + box.width / 2
            distance = abs(box_cx - region_cx)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                level = parsed_level
                target_name = name
                target_box = box
        if level is None:
            # 区域里没有明确的档位文字，按钮不在当前界面或被遮挡，不点击；
            # 挂起的点击验证随之作废，等档位文字再次出现时重新尝试
            self._speed_pending = None
            return
        if level == self.GARDEN_SPEED_MAX_LEVEL:
            if not self._speed_max_logged:
                self.log_info('garden speed is MAX')
                self._speed_max_logged = True
            self._speed_pending = None
            self._speed_stuck = 0
            self._speed_cooldown_until = 0.0
            return
        self._speed_max_logged = False
        # 验证上一次点击：至少间隔 1 秒后档位仍未上升，判为该界面点击无效
        click_blocked = False
        if self._speed_pending is not None:
            prev_level, clicked_at = self._speed_pending
            if now - clicked_at < 1.0:
                # 距上次点击太近，等下一轮观测
                return
            self._speed_pending = None
            if level > prev_level:
                # 点击生效：清零无效计数，并在本轮继续点向下一档
                self._speed_stuck = 0
            else:
                self._speed_stuck += 1
                click_blocked = True
                self.log_debug(
                    f'garden speed click had no effect, still {target_name}, stuck={self._speed_stuck}'
                )
                if self._speed_stuck >= 3:
                    self._speed_stuck = 0
                    self._speed_cooldown_until = now + 12
                    self.log_info('garden speed button does not respond on this screen, retry later')
        # 点击无效的观测轮、或处于冷却期时，不发起新点击
        if click_blocked or now < self._speed_cooldown_until:
            return
        self._speed_pending = (level, now)
        self.log_info(f'garden speed shows {target_name}, click to cycle toward MAX')
        self.click(target_box, after_sleep=0.6)

    def find_best_garden_feature(self):
        matches = []
        for feature_name in self.garden_features:
            if not self.feature_exists(feature_name):
                continue
            if feature_name == 'garden_get_confirm_gray' or feature_name == 'garden_not_interested_confirm':
                continue
            if feature_name == 'garden_not_interested':
                matches.extend(self.find_feature(feature_name, vertical_variance=0.4))
            else:
                matches.extend(self.find_feature(feature_name))
        for priority_feature in self.garden_priority_features:
            priority_matches = [
                match for match in matches
                if match.name == priority_feature
            ]
            if priority_matches:
                return max(priority_matches, key=lambda box: box.confidence)
        return max(matches, key=lambda box: box.confidence, default=None)

    def _choose_first_blessing(self):
        """At Garden Entrance, choose first blessing"""
        # click blessing botton
        self.click(965 / 1920, 860 / 1080, after_sleep=2)
        # choose blessing1(Add-on)
        self.click(700 / 1920, 666 / 1080, after_sleep=2)
        # confirm
        self.click(1600 / 1920, 900 / 1080, after_sleep=2)


if __name__ == "__main__":
    run_task(config, task=GardenTask, debug=True)
