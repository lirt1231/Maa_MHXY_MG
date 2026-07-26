
from PIL import Image
import os
from maa.custom_recognition  import CustomRecognition
from maa.context import Context
from typing import Dict, List, Tuple
from utils import logger
import time
import re

from .searchAnswer import SearchQuestions


if os.environ.get("MAA_DIRECT_RESOURCE") != "1":
    from maa.agent.agent_server import AgentServer
else:
    AgentServer = None


def register_custom_recognition(name):
    """Use AgentServer normally, or leave registration to a direct runner."""
    if os.environ.get("MAA_DIRECT_RESOURCE") == "1":
        return lambda recognition: recognition
    return AgentServer.custom_recognition(name)


LEGACY_REFERENCE_SIZE = (1280, 720)
MAC_REFERENCE_SIZE = (923, 720)
LEGACY_QUESTION_ROI = (447, 40, 673, 94)
LEGACY_ANSWER_ROI = (439, 218, 678, 212)
# The macOS question wraps to two lines. The first line starts around y=133
# after system-window capture normalization, so keep enough space above it.
MAC_QUESTION_ROI = (330, 125, 480, 90)
MAC_ANSWER_ROI = (330, 205, 480, 220)


def scale_roi(roi, image_width, image_height, reference_size):
    """Scale a reference ROI to the controller's current screenshot."""
    reference_width, reference_height = reference_size
    scale_x = image_width / reference_width
    scale_y = image_height / reference_height
    x, y, width, height = roi
    return [
        round(x * scale_x),
        round(y * scale_y),
        round(width * scale_x),
        round(height * scale_y),
    ]


def sjqy_rois(image):
    """Return question/answer ROIs for emulator or the narrow macOS client."""
    image_height, image_width = image.shape[:2]
    if image_width / image_height < 1.5:
        reference_size = MAC_REFERENCE_SIZE
        question_roi = MAC_QUESTION_ROI
        answer_roi = MAC_ANSWER_ROI
    else:
        reference_size = LEGACY_REFERENCE_SIZE
        question_roi = LEGACY_QUESTION_ROI
        answer_roi = LEGACY_ANSWER_ROI
    return (
        scale_roi(question_roi, image_width, image_height, reference_size),
        scale_roi(answer_roi, image_width, image_height, reference_size),
    )


def parse_answer_candidates(value):
    """Convert the legacy formatted answer string into OCR candidates."""
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    parsed = re.findall(r'"([^"]+)"', str(value))
    return parsed or ([str(value).strip()] if str(value).strip() else [])

@register_custom_recognition("reco2")
class reco2_sjqy(CustomRecognition):
    def analyze(
         self,
         context: Context,
         argv: CustomRecognition.AnalyzeArg,
     ) -> CustomRecognition.AnalyzeResult:
        logger.info("进入reco2_sjqy")
        return CustomRecognition.AnalyzeResult(box=(6,134,57,46),detail="福利")

 

@register_custom_recognition("sjqy_tiku_V2")
class sjqy_tiku_V2(CustomRecognition):

    def analyze(
         self,
         context: Context,
         argv: CustomRecognition.AnalyzeArg,
     ) -> CustomRecognition.AnalyzeResult:
        logger.info("进入三界奇缘答题agent_V2")

            # 根据位置排序OCR结果
        def sort_ocr_results_by_position(ocr_results):
            # 定义行高阈值，如果两个框的y坐标差距小于这个值，认为它们在同一行
            row_height_threshold = 20
            
            # 按y坐标分组（将接近的y坐标视为同一行）
            rows = {}
            for result in ocr_results:
                y = result.box[1]  # y坐标
                assigned = False
                
                # 检查是否可以分配到现有行
                for row_y in rows.keys():
                    if abs(y - row_y) < row_height_threshold:
                        rows[row_y].append(result)
                        assigned = True
                        break
                
                # 如果不能分配到现有行，创建新行
                if not assigned:
                    rows[y] = [result]
            
            # 对每一行内的元素按x坐标排序
            for row_y in rows:
                rows[row_y].sort(key=lambda r: r.box[0])
            
            # 按y坐标对行进行排序，并将所有结果合并到一个列表中
            sorted_results = []
            for row_y in sorted(rows.keys()):
                sorted_results.extend(rows[row_y])
            
            return sorted_results

        i = 0
        # 未找到答案次数
        NotAnswerCount = 0
        while i < 30:
            i = i+1
            # logger.info(f"第{i}次识别三界奇缘题目")
            #识别三界奇缘题目
            image1 = context.tasker.controller.post_screencap().wait().get()
            reco_detail = context.run_recognition(
                "三界奇缘题目",
                image1,
                pipeline_override={"三界奇缘题目": {"roi" : [447,40,673,94],
                                                    "expected":[""],
                                                    "recognition": "OCR"
                                                    }
                                    }
                )
            #识别题目返回值
            # logger.info("reco_detail为：{reco_detail}")
            
            # logger.info(reco_detail.box)
            # 没有识别到题目
            if not reco_detail or not reco_detail.hit:
                # logger.info("没有识别到题目")
                logger.info(f"未在题库中搜索到答案次数:{NotAnswerCount}，请反馈开发者填充题库。")
                return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="答题结束")
            all_results= reco_detail.all_results
            #按照box进行排序
            sorted_results= sort_ocr_results_by_position(all_results)
            #整合题目
            ext=''
            for item in sorted_results:
                if item.text!='':
                    ext+=item.text
            pattern = r'第\d+题：|[（(]\d+/\d+[）)]'
            def clean_string(s):
                return re.sub(pattern, '', s).strip()
            text = clean_string(ext)
            # 获取答案list[]
            results_value, confidence ,match_type = SearchQuestions(text)
            # 如果可信度为零，点击第一个答案
            if confidence == 0:
                # logger.info(f"题库中未找到答案，问题为:{text}.请反馈开发者填充题库")
                NotAnswerCount = NotAnswerCount + 1
                time.sleep(2)
                context.tasker.controller.post_click(500, 344).wait()
                time.sleep(3)
                continue
        
            # 识别答案位置
            # new_context = context.clone()
            # image2 = context.tasker.controller.post_screencap().wait().get()
            new_reco_detail = context.run_recognition(
                            "三界奇缘答案位置",
                            image1,
                            pipeline_override={"三界奇缘答案位置": {"roi" : [439,218,678,212],
                                                                "expected":results_value,
                                                                "recognition": "OCR"
                                                                }
                                                }
                            )
            # logger.info("new_reco_detail为：{new_reco_detail}")
            # logger.info(new_reco_detail.box)
            # 点击答案
            if new_reco_detail and new_reco_detail.hit:
                box = new_reco_detail.box  # 假设box为(x, y, w, h)
                center_x = box[0] + box[2] // 2
                center_y = box[1] + box[3] // 2 
                time.sleep(2)
                click_job = context.tasker.controller.post_click(center_x, center_y)
                click_job.wait()  # 等待点击操作完成
            
                time.sleep(2)
            else:#没找到答案，点击的一个
                time.sleep(2)
                context.tasker.controller.post_click(500, 344).wait()
                time.sleep(1)

        logger.info(f"未在题库中搜索到答案次数:{NotAnswerCount}，请反馈开发者填充题库。")
        return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="答题结束")

@register_custom_recognition("sjqy_tiku_V3")
class sjqy_tiku_V3(CustomRecognition):

    def analyze(
         self,
         context: Context,
         argv: CustomRecognition.AnalyzeArg,
     ) -> CustomRecognition.AnalyzeResult:
        # logger.info("进入三界奇缘答题agent_V3")

            # 根据位置排序OCR结果
        def sort_ocr_results_by_position(ocr_results):
            # 定义行高阈值，如果两个框的y坐标差距小于这个值，认为它们在同一行
            row_height_threshold = 10
            
            # 按y坐标分组（将接近的y坐标视为同一行）
            rows = {}
            for result in ocr_results:
                y = result.box[1]  # y坐标
                assigned = False
                
                # 检查是否可以分配到现有行
                for row_y in rows.keys():
                    if abs(y - row_y) < row_height_threshold:
                        rows[row_y].append(result)
                        assigned = True
                        break
                
                # 如果不能分配到现有行，创建新行
                if not assigned:
                    rows[y] = [result]
            
            # 对每一行内的元素按x坐标排序
            for row_y in rows:
                rows[row_y].sort(key=lambda r: r.box[0])
            
            # 按y坐标对行进行排序，并将所有结果合并到一个列表中
            sorted_results = []
            for row_y in sorted(rows.keys()):
                sorted_results.extend(rows[row_y])
            
            return sorted_results

        # 未找到答案次数
        NotAnswerCount = 0
        # 当第一次进入答题时，需要暂停一秒后在开始截图。
        max_hit = context.get_hit_count("活动-三界奇缘-开始答题_agent")
        if max_hit == 0:
            time.sleep(1.5)
        
        #识别三界奇缘题目
        image1 = context.tasker.controller.post_screencap().wait().get()
        question_roi, answer_roi = sjqy_rois(image1)
        reco_detail = context.run_recognition(
            "三界奇缘题目",
            image1,
            pipeline_override={"三界奇缘题目": {"roi" : question_roi,
                                                "expected":[".*"],
                                                "recognition": "OCR",
                                                "replace":[
                                                    ["味","昧"],
                                                    ["邮","邺"],
                                                    ["尺","尸"],
                                                    ["频","濒"],
                                                    ["铜","锢"]
                                                ]
                                                }
                                }
            )
        #识别题目返回值
        # logger.info("reco_detail为：{reco_detail}")
        
        # logger.info(reco_detail.box)
        # 没有识别到题目
        if not reco_detail or not reco_detail.hit:
            logger.info("没有识别到题目")
            # logger.info(f"未在题库中搜索到答案次数:{NotAnswerCount}，请反馈开发者填充题库。")
            return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="答题结束")
        filtered_results= reco_detail.filtered_results
        #按照box进行排序
        sorted_results= sort_ocr_results_by_position(filtered_results)
        #整合题目
        ext=''
        for item in sorted_results:
            if item.text!='':
                ext+=item.text
        question_key = ext.strip()
        pattern = r'第\d+题：|[（(]\d+/\d+[）)]'
        def clean_string(s):
            return re.sub(pattern, '', s).strip()
        text = clean_string(ext)
        # 获取答案list[]
        results_value, confidence ,match_type = SearchQuestions(text)
        answer_candidates = parse_answer_candidates(results_value)

        if question_key == getattr(self, "last_question_key", None):
            self.last_status = "unchanged"
            logger.error(f"题目未变化，停止重复点击：{text}")
            return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="题目未变化")

        if confidence < 80:
            self.last_status = "low_confidence"
            logger.error(f"题库匹配度不足，停止答题：{text}，匹配度：{confidence}")
            return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="题库缺少本问题")
        elif confidence >=80 and confidence < 100:
            logger.warning(f"本问题与题库非100%匹配：{text}，匹配度：{confidence}")
        # 识别答案位置
        # new_context = context.clone()
        # image2 = context.tasker.controller.post_screencap().wait().get()
        new_reco_detail = context.run_recognition(
                        "三界奇缘答案位置",
                        image1,
                        pipeline_override={"三界奇缘答案位置": {"roi" : answer_roi,
                                                            "expected":answer_candidates,
                                                            "recognition": "OCR"
                                                            }
                                            }
                        )
        # logger.info("new_reco_detail为：{new_reco_detail}")
        # logger.info(new_reco_detail.box)
        # 点击答案
        if new_reco_detail and new_reco_detail.hit:
            box = new_reco_detail.box  # 假设box为(x, y, w, h)
            center_x = box[0] + box[2] // 2
            center_y = box[1] + box[3] // 2 
            if os.environ.get("MAA_SJQY_RECOGNIZE_ONLY") == "1":
                self.last_question = text
                self.last_question_key = question_key
                self.last_status = "ready"
                self.last_click = (center_x, center_y)
                self.last_image_size = (image1.shape[1], image1.shape[0])
                logger.info(
                    f"识别完成，等待外部输入桥接。题目：{text}。答案：{answer_candidates}。"
                    f"答案框：{box}。点击坐标：({center_x}, {center_y})。"
                )
                return CustomRecognition.AnalyzeResult(box=box, detail="答案坐标已就绪")
            time.sleep(2)
            click_job = context.tasker.controller.post_click(center_x, center_y)
            click_result = click_job.wait()
            if not click_result.succeeded:
                self.last_status = "click_failed"
                logger.error(f"点击任务失败：{text}，坐标：({center_x}, {center_y})")
                return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="点击失败")
            self.last_question = text
            self.last_question_key = question_key
            self.last_status = "clicked"
            # image2 = context.tasker.controller.post_screencap().wait().get()
            # logger.info(image2)
            logger.info(
                f"[color:blue]正确点击答案。[/color]识别题目：{text}。"
                f"题库答案：{results_value}。匹配度：{confidence}。"
                f"答案框：{box}。点击坐标：({center_x}, {center_y})。答案ROI：{answer_roi}。"
            )
            time.sleep(2)
        else:
            self.last_status = "answer_not_found"
            logger.error(f"未在界面找到题库答案，停止答题。识别题目：{text}。题库答案：{answer_candidates}。匹配度：{confidence}。")
            return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="界面未找到答案")

        # logger.info(f"未在题库中搜索到答案次数:{NotAnswerCount}，请反馈开发者填充题库。")
        return CustomRecognition.AnalyzeResult(box=(0,0,0,0),detail="答题结束")
