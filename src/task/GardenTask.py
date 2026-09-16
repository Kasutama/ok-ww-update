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
    GARDEN_SPEED_LOW = re.compile(r'(?:[xX×]\s*)?[135](?:\.0)?')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.name = "🎡 自动周常乐园"
        self.description = "Detect and click garden actions until the task is stopped."
        # 速度档位检测节流：两次检测至少间隔秒数
        self._last_speed_check = 0.0
        self._speed_max_logged = False
        self._speed_clicks = 0
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
                    self.click(self.get_box_by_name('garden_get_confirm_gray'), after_sleep=1)
                    continue
                elif target.name == 'garden_not_interested':
                    not_interested = self.find_feature('garden_not_interested', vertical_variance=0.4)
                    self.click(not_interested[-1], after_sleep=1)
                    self.click(self.get_box_by_name('garden_not_interested_confirm'), after_sleep=1)
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

    def ensure_garden_speed_max(self, force=False):
        """检测顶部居中的速度胶囊按钮（×1.0/×3.0/×5.0/MAX），不是 MAX 就点一下。

        OCR 明确读到档位文字才点击，读不到（界面加载中/被弹窗遮挡）绝不盲点，
        防止把已有的 MAX 档点回 ×1；连续点击上限后放弃并打印原因。
        """
        now = time.monotonic()
        if not force and now - self._last_speed_check < 2:
            return
        self._last_speed_check = now
        # 实测 1920x1040 主界面按钮位于 (1219,41)-(1343,82)，中心约 (1281,61)；
        # 与 COCO the_garden_max 锚点 (1227,43,71,38) 基本重合，按 hcenter 锚定
        speed_box = self.box_of_screen(0.605, 0.02, 0.725, 0.10, hcenter=True, vcenter=True)
        try:
            texts = self.ocr(box=speed_box)
        except Exception as e:
            self.log_debug(f'garden speed ocr failed: {e}')
            return
        names = [str(getattr(box, 'name', box)).strip() for box in texts]
        if any(self.GARDEN_SPEED_MAX.fullmatch(name) for name in names):
            if not self._speed_max_logged:
                self.log_info('garden speed is MAX')
                self._speed_max_logged = True
            self._speed_clicks = 0
            return
        low = next((box for box in texts
                    if self.GARDEN_SPEED_LOW.fullmatch(str(getattr(box, 'name', box)).strip())), None)
        if low is None:
            # 区域里没有明确的档位文字，说明按钮不在当前界面或被遮挡，不点击
            return
        if self._speed_clicks >= 6:
            if self._speed_clicks == 6:
                self.log_error(
                    f'garden speed still shows {getattr(low, "name", low)} after {self._speed_clicks} clicks, '
                    'stop cycling', notify=True
                )
            return
        self._speed_max_logged = False
        self._speed_clicks += 1
        self.log_info(
            f'garden speed shows {getattr(low, "name", low)}, click to cycle (click #{self._speed_clicks})'
        )
        self.click(low, after_sleep=0.6)

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
