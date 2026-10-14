import cv2, numpy as np, mediapipe as mp, glob, os
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

model_path = os.path.join(os.path.dirname(__file__), '..', 'selfie_segmenter.tflite')
options = vision.ImageSegmenterOptions(
    base_options=mp_python.BaseOptions(model_asset_path=model_path),
    output_category_mask=True,
    running_mode=vision.RunningMode.IMAGE,
)
segmentor = vision.ImageSegmenter.create_from_options(options)

files = glob.glob('downloads/instagram/oliverpinedadance/*.mp4')
src = files[0]
print(f'Processing: {src}', flush=True)

cap = cv2.VideoCapture(src)
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
max_frames = min(total, int(fps * 10))

out = cv2.VideoWriter('blur_demo.mp4', cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))

for i in range(max_frames):
    ret, frame = cap.read()
    if not ret:
        break
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = segmentor.segment(mp_image)
    mask = result.category_mask.numpy_view().squeeze()
    person_mask = (mask > 0).astype(np.uint8)[:, :, np.newaxis].repeat(3, axis=2)
    blurred = cv2.GaussianBlur(frame, (51, 51), 0)
    out.write(np.where(person_mask, frame, blurred))
    if (i + 1) % 30 == 0:
        print(f'  {i+1}/{max_frames}', flush=True)

cap.release()
out.release()
print(f'Done! blur_demo.mp4', flush=True)
