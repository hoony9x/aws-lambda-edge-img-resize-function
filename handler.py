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
        target_quality = 75

        # 변환 대상 값을 parsing
        if request["querystring"] != "":
            query_strings = [tuple(query_string.split("=")) for query_string in request["querystring"].split("&")]
            for k, v in query_strings:
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

        qs = f"q{target_quality}_"
        if target_width is not None:
            qs = f"w{target_width}{qs}"

        if target_height is not None:
            qs = f"h{target_height}{qs}"

        s3_object_key = request["uri"][1:]
        s3_object_key_split = s3_object_key.split("/")
        s3_object_key_split[-1] = qs + s3_object_key_split[-1]
        converted_object_key = "/".join(s3_object_key_split)

        file_extension = s3_object_key.split("/")[-1].split(".")[-1]
        if file_extension.lower() == "png":
            converted_object_key = converted_object_key[:-3] + "jpg"

        is_converted_object_exists = True
        try:
            s3_response = s3_client.head_object(
                Bucket=s3_bucket_name,
                Key=converted_object_key
            )
        except ClientError:
            is_converted_object_exists = False

        if is_converted_object_exists is True:
            response["status"] = 301
            response["statusDescription"] = "Moved Permanently"
            response["headers"] = {
                "location": [
                    {
                        "key": "Location",
                        "value": f"/{converted_object_key}"
                    }
                ]
            }
        else:
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
                decrease_ratio = 1.0

            if original_image.format == "JPEG":
                converted_image = original_image.resize(
                    (int(width * decrease_ratio), int(height * decrease_ratio)),
                    reducing_gap=3
                )
            elif original_image.format == "PNG":
                white_background_img = Image.new("RGBA", original_image.size, "WHITE")
                white_background_img.paste(original_image, (0, 0), original_image)
                converted_image = white_background_img.convert("RGB").resize(
                    (int(width * decrease_ratio), int(height * decrease_ratio)),
                    reducing_gap=3
                )
            else:
                # pass through
                return response

            # https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.tobytes
            # 위 링크에서는 Compressed Image 에서 .tobytes() 사용 시 이미지가 제대로 저장되지 않는다고 하고 있음.
            bytes_io = io.BytesIO()
            converted_image.save(bytes_io, format="JPEG", optimize=True, quality=target_quality)
            result_size = bytes_io.tell()
            result_data = bytes_io.getvalue()
            result = base64.standard_b64encode(result_data).decode()
            bytes_io.close()

            converted_image.close()
            original_image.close()

            if result_size > 1000 * 1000:
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
                response["headers"] = {
                    "location": [
                        {
                            "key": "Location",
                            "value": f"/{converted_object_key}"
                        }
                    ]
                }
            else:
                response["status"] = 200
                response["statusDescription"] = "OK"
                response["body"] = result
                response["bodyEncoding"] = "base64"
                response["headers"] = {
                    "content-type": [
                        {
                            "key": "Content-Type",
                            "value": "image/jpeg"
                        }
                    ]
                }

    return response