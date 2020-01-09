from botocore.exceptions import ClientError
from PIL import Image
import boto3

from typing import Optional, List, Tuple
import base64
import io

# Lambda@Edge 에서는 환경 변수의 사용이 불가능하기 때문에 직접 코드 내에서 지정을 해야 한다.
s3_bucket_name: str = "hoony9x-test"
s3_client = boto3.client("s3")


def lambda_handler(event, context):
    record: dict = event["Records"][0]["cf"]
    request: dict = record["request"]
    response: dict = record["response"]

    # 요청한 파일이 존재하지 않을 경우 status 가 40X 의 형태일 것이다.
    if int(response["status"]) == 200:
        target_width: Optional[int] = None
        target_height: Optional[int] = None
        target_quality: int = 75

        # 변환 대상 값을 parsing
        if request["querystring"] != "":
            queries: List[Tuple[str, str]] = [tuple(q_str.split("=")) for q_str in request["querystring"].split("&")]
            for k, v in queries:
                if k == "w":
                    target_width = int(v)
                elif k == "h":
                    target_height = int(v)
                elif k == "q":
                    target_quality = int(v)
                    if target_quality > 95:
                        target_quality = 95
                    elif target_quality < 1:
                        target_quality = 1

        # 변환 대상의 가로세로 값이 둘 다 주어지지 않을 경우 그대로 pass through
        if target_width is None and target_height is None:
            return response

        qs: str = f"q{target_quality}_"
        if target_width is not None:
            qs = f"w{target_width}{qs}"

        if target_height is not None:
            qs = f"h{target_height}{qs}"

        s3_object_key: str = request["uri"][1:]
        s3_object_key_split: List[str] = s3_object_key.split("/")
        s3_object_key_split[-1] = qs + s3_object_key_split[-1]

        # Lambda@Edge 에서는 Body Size 가 1MB 를 넘을 수 없다.
        # 따라서 변환 결과물이 1MB 가 넘을 경우 s3 에 해당 결과물을 올리게 된다.
        # converted_object_key 는 해당 결과물의 파일명에 해당함.
        converted_object_key: str = "/".join(s3_object_key_split)

        # 본 코드에서는 JPEG 가 아닌 이미지 파일(현재는 PNG 파일만 해당) 을 JPG 로 변환하고 있음.
        # 따라서 해당하는 경우 .jpg 확장자를 붙혀주게 된다.
        file_extension: str = s3_object_key.split("/")[-1].split(".")[-1]
        if file_extension.lower() == "png":
            converted_object_key = converted_object_key[:-3] + "jpg"

        # 변환 결과물이 이미 존재하는지 확인.
        is_converted_object_exists: bool = True
        try:
            s3_response = s3_client.head_object(
                Bucket=s3_bucket_name,
                Key=converted_object_key
            )
        except ClientError:
            is_converted_object_exists = False

        if is_converted_object_exists is True:
            # 변환 결과물이 이미 있는 경우 해당하는 파일의 링크를 301 redirect 로 넘겨준다.
            response["status"] = 301
            response["statusDescription"] = "Moved Permanently"
            response["body"] = ""
            response["headers"]["location"] = [{"key": "Location", "value": f"/{converted_object_key}"}]
        else:
            try:
                s3_response = s3_client.get_object(
                    Bucket=s3_bucket_name,
                    Key=s3_object_key
                )
            except ClientError as e:
                raise e

            # JPEG 나 PNG 가 아닐 경우 pass through
            s3_object_type: str = s3_response["ContentType"]
            if s3_object_type not in ["image/jpeg", "image/png"]:
                return response

            # 원래 이미지 불러오기
            original_image = Image.open(s3_response["Body"])
            width, height = original_image.size

            target_width: int = width if target_width is None else target_width
            target_height: int = height if target_height is None else target_height

            w_decrease_ratio: float = target_width / width
            h_decrease_ratio: float = target_height / height

            # 축소 비율이 덜한 쪽으록 기준을 잡는다.
            transform_ratio: float = max(w_decrease_ratio, h_decrease_ratio)
            # if transform_ratio > 1.0:
            #     transform_ratio = 1.0

            if original_image.format == "JPEG":
                converted_image = original_image.resize(
                    (int(width * transform_ratio), int(height * transform_ratio)),
                    reducing_gap=3
                )
            elif original_image.format == "PNG":
                # PNG 일 경우 강제로 JPG 로 변환한다.
                white_background_img = Image.new("RGBA", original_image.size, "WHITE")
                white_background_img.paste(original_image, (0, 0), original_image)
                converted_image = white_background_img.convert("RGB").resize(
                    (int(width * transform_ratio), int(height * transform_ratio)),
                    reducing_gap=3
                )
            else:
                # pass through
                return response

            mid_x = converted_image.size[0] / 2
            mid_y = converted_image.size[1] / 2
            diff_x = target_width / 2
            diff_y = target_height / 2

            start_x = int(round(mid_x - diff_x))
            if start_x < 0:
                start_x = 0

            start_y = int(round(mid_y - diff_y))
            if start_y < 0:
                start_y = 0

            end_x = int(round(mid_x + diff_x))
            if end_x >= target_width:
                end_x = target_width - 1

            end_y = int(round(mid_y + diff_y))
            if end_y >= target_height:
                end_y = target_height - 1

            cropped_image = converted_image.crop((start_x, start_y, end_x, end_y))

            # https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.tobytes
            # 위 링크에서는 Compressed Image 에서 .tobytes() 사용 시 이미지가 제대로 저장되지 않는다고 하고 있음.
            bytes_io = io.BytesIO()
            cropped_image.save(bytes_io, format="JPEG", optimize=True, quality=target_quality)
            result_size: int = bytes_io.tell()
            result_data: bytes = bytes_io.getvalue()
            result: str = base64.standard_b64encode(result_data).decode()
            bytes_io.close()

            converted_image.close()
            original_image.close()

            if result_size > 1000 * 1000:
                # 결과물이 1MB 를 넘을 경우 (정확히는 1024 * 1024 로 해야 하지만 혹시 모르니..)
                # 결과물을 S3 에 넣은 후 해당 파일의 링크를 301 redirect 로 넘겨준다.
                try:
                    s3_response = s3_client.put_object(
                        Bucket=s3_bucket_name,
                        Key=converted_object_key,
                        ContentType="image/jpeg",
                        Body=result_data
                    )
                except ClientError as e:
                    raise e

                response["status"] = 301
                response["statusDescription"] = "Moved Permanently"
                response["body"] = ""
                response["headers"]["location"] = [{"key": "Location", "value": f"/{converted_object_key}"}]
            else:
                # 1MB 미만이라면 결과값을 그대로 response body 에 넣어서 보내준다.
                response["status"] = 200
                response["statusDescription"] = "OK"
                response["body"] = result
                response["bodyEncoding"] = "base64"
                response["headers"]["content-type"] = [{"key": "Content-Type", "value": "image/jpeg"}]

    return response
