import boto3
from botocore.exceptions import ClientError
from PIL import Image
import base64
import io

s3_bucket_name = "hoony9x-test"
s3_client = boto3.client("s3")


def lambda_handler(event, context):
    record = event["Records"][0]["cf"]
    request = record["request"]
    response = record["response"]

    if int(response["status"]) == 200:
        target_width = None
        target_height = None

        # 변환 대상 값을 parsing
        if request["querystring"] != "":
            query_strings = [tuple(query_string.split("=")) for query_string in request["querystring"].split("&")]
            for query_string in query_strings:
                k, v = query_string
                if k == "w":
                    target_width = int(v)
                elif k == "h":
                    target_height = int(v)

        # 변환 대상의 가로세로 값이 둘 다 주어지지 않을 경우 그대로 pass through
        if target_width is None and target_height is None:
            return response

        qs = request["querystring"].replace("=", "").replace("&", "")
        s3_object_key = request["uri"][1:]

        # 우선 변환된 파일(결과물이 1MB 이상인 경우)이 존재하는지 여부 확인.
        is_exists = True
        try:
            s3_response = s3_client.get_object(
                Bucket=s3_bucket_name,
                Key=f"{qs}_{s3_object_key}"
            )
        except ClientError as e:
            is_exists = False

        if is_exists is True:
            response["status"] = 301
            response["statusDescription"] = "Moved Permanently"
            response["headers"] = {
                "location": [
                    {
                        "key": "Location",
                        "value": f"/{qs}_{s3_object_key}"
                    }
                ]
            }

            return response

        try:
            s3_response = s3_client.get_object(
                Bucket=s3_bucket_name,
                Key=s3_object_key
            )
        except ClientError as e:
            raise e

        # JPEG 나 PNG 가 아닐 경우 pass through
        s3_object_type = s3_response["ContentType"]
        if s3_object_type not in ["image/jpeg", "image/png"]:
            return response

        original_image = Image.open(s3_response["Body"])
        width, height = original_image.size

        target_width = width if target_width is None else target_width
        target_height = height if target_height is None else target_height

        w_decrease_ratio = target_width / width
        h_decrease_ratio = target_height / height

        decrease_ratio = min(w_decrease_ratio, h_decrease_ratio)
        if decrease_ratio > 1.0:
            return response

        if original_image.format == "JPEG":
            converted_image = original_image.resize(
                (int(width * decrease_ratio), int(height * decrease_ratio)),
                reducing_gap=3
            )
        else:
            converted_image = original_image.convert(mode="P").resize(
                (int(width * decrease_ratio), int(height * decrease_ratio)),
                reducing_gap=3
            )

        # https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.tobytes
        # 위 링크에서는 Compressed Image 에서 .tobytes() 사용 시 이미지가 제대로 저장되지 않는다고 하고 있음.
        bytes_io = io.BytesIO()

        # PNG 파일은 quality 옵션이 먹히지 않음.
        converted_image.save(bytes_io, format=original_image.format, optimize=True, quality=80)
        result_size = bytes_io.tell()
        result_data = bytes_io.getvalue()
        result = base64.standard_b64encode(result_data).decode()

        bytes_io.close()

        converted_image.close()
        original_image.close()

        if result_size >= 1000 * 1000:
            new_object_key = f"{qs}_{s3_object_key}"
            try:
                s3_client.put_object(
                    Bucket=s3_bucket_name,
                    ContentType=s3_object_type,
                    Key=new_object_key,
                    Body=result_data
                )
            except ClientError as e:
                raise e

            response["status"] = 301
            response["statusDescription"] = "Moved Permanently"
            response["headers"] = {
                "location": [
                    {
                        "key": "Location",
                        "value": f"/{new_object_key}"
                    }
                ]
            }
        else:
            response["status"] = 200
            response["statusDescription"] = "OK"
            response["body"] = result
            response["bodyEncoding"] = "base64"
            response["headers"]["content-type"] = [{"key": "Content-Type", "value": s3_object_type}]

    return response
