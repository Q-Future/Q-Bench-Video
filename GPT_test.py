import argparse
import json
import io
import base64
from PIL import Image
import torch
import decord
from tqdm import tqdm
from openai import OpenAI

MAX_RETRY_TIMES = 5

def load_video(video_file, num_frames=16):
    vr = decord.VideoReader(video_file, num_threads=1)
    total_valid_frames = len(vr)
    fps = vr.get_avg_fps()
    frame_indices = [int(total_valid_frames / num_frames) * i for i in range(num_frames)]
    frames = vr.get_batch(frame_indices).asnumpy()
    return [Image.fromarray(fr).convert("RGB") for fr in frames], [frame_index / fps for frame_index in frame_indices]

def resize_image(image_obj, max_length=512):
    width, height = image_obj.size
    scaling_factor = min(max_length / width, max_length / height)
    if scaling_factor < 1:
        return image_obj.resize((int(width * scaling_factor), int(height * scaling_factor)))
    return image_obj

def encode_pil_image_to_base64(image):
    image = resize_image(image)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode('utf-8')

def make_interleave_content(texts_or_image_paths):
    content = []
    for item in texts_or_image_paths:
        if isinstance(item, Image.Image):
            base64_image = encode_pil_image_to_base64(item)
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "low"}})
        elif isinstance(item, str):
            if item.startswith("<|image|>"):
                base64_image = encode_pil_image_to_base64(item.replace("<|image|>", ""))
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "low"}})
            else:
                content.append({"type": "text", "text": item})
    return content

def request(api_key, texts_or_image_paths, timeout=60):
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": make_interleave_content(texts_or_image_paths)}],
        max_tokens=1000,
        timeout=timeout,
    )
    return response.choices[0].message.content

def main(json_file, video_dir, output_file, api_key):
    with open(json_file, "r") as f:
        data = json.load(f)

    pbar = tqdm(total=len(data))
    
    for item in data:
        if 'answer' in item:
            continue
        video_file = item["video_name"]
        message = item["question"] + "\n"
        
        if 'open_ended' in item['question_type']:
            message = [message]
        else:
            options = item.get("answers", [])
            message += "".join([f"{choice} {ans}\n" for choice, ans in zip(["A.", "B.", "C.", "D."][:len(options)], options)])
            message = [message, "Please answer the question in the following format: the uppercase letter of the correct answer option itself +'.'. Please do not add any other answers beyond this."]

        if 'Compare' in item['context'] or 'Joint' in item['context']:
            prefix_text = "You will receive 16 distinct frames in total. The first 8 frames and 9-16 frames are uniformly sampled from the first and the second video sequence, arranged in the same temporal order as they appear in the videos. Please analyze these frames and answer the questions based on your observations."
            frames1, _ = load_video(f'{video_dir}/{video_file.split("_cat_")[0]}', num_frames=8)
            frames2, _ = load_video(f'{video_dir}/{video_file.split("_cat_")[1][:-4]}', num_frames=8)
            prompt_list = [prefix_text, '\n', 'The first video frames:'] + frames1 + ['The second video frames:'] + frames2 + message
        else:
            prefix_text = "You will receive 16 distinct frames that have been uniformly sampled from a video sequence, arranged in the same temporal order as they appear in the video. Please analyze these frames and answer the questions based on your observations."
            frames, _ = load_video(f'{video_dir}/{video_file}', num_frames=16)
            prompt_list = [prefix_text, '\n', 'The video frames:'] + frames + message

        response = request(api_key, prompt_list, timeout=60)
        item['answer'] = response

        pbar.update(1)

    with open(output_file, "w") as f:
        json.dump(data, f)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process video data.")
    parser.add_argument('--json_file', required=True, help='Path to the input JSON file.')
    parser.add_argument('--video_dir', required=True, help='Directory containing video files.')
    parser.add_argument('--output_file', required=True, help='Path to the output JSON file.')
    parser.add_argument('--api_key', required=True, help='OpenAI API key.')

    args = parser.parse_args()
    main(args.json_file, args.video_dir, args.output_file, args.api_key)
