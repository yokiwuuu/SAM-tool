from fastapi import FastAPI, status, File, Form, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from starlette.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware

from segment_anything import SamAutomaticMaskGenerator, sam_model_registry, SamPredictor
import cv2
import zipfile
import numpy as np
from io import BytesIO
from PIL import Image
from base64 import b64encode, b64decode

def pil_image_to_base64(image):
    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = b64encode(buffered.getvalue()).decode("utf-8")
    return img_str

def read_content(file_path: str) -> str:
    """read the content of target file
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return content
sam_checkpoint = "sam_vit_l_0b3195.pth" # "sam_vit_l_0b3195.pth" or "sam_vit_h_4b8939.pth"
model_type = "vit_l" # "vit_l" or "vit_h"
device = "cpu"
# sam_checkpoint = "sam_vit_l_0b3195.pth" # "sam_vit_l_0b3195.pth" or "sam_vit_h_4b8939.pth"
# model_type = "vit_l" # "vit_l" or "vit_h"
# device = "cuda" # "cuda" if torch.cuda.is_available() else "cpu"

print("Loading model")
sam = sam_model_registry[model_type](checkpoint=sam_checkpoint).to(device)
print("Finishing loading")
predictor = SamPredictor(sam)
mask_generator = SamAutomaticMaskGenerator(sam)

app = FastAPI(debug=True)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

input_point = []
input_label = []
masks = []
mask_input = [None]

GLOBAL_IMAGE = None
GLOBAL_MASK = None
GLOBAL_ZIPBUFFER = None
GLOBAL_ORIGIN_IMAGE = None
@app.post("/image")
async def process_images(
    image: UploadFile = File(...)
):
    global input_point, input_label, mask_input, masks
    global GLOBAL_IMAGE, GLOBAL_MASK, GLOBAL_ZIPBUFFER,GLOBAL_ORIGIN_IMAGE

    input_point = []
    input_label = []
    masks = []
    # mask_input = [None]

    # Read the image and mask data as bytes
    image_data = await image.read()

    image_data = BytesIO(image_data)
    img = np.array(Image.open(image_data))
    GLOBAL_ORIGIN_IMAGE = img
    print("get image", img.shape)
    GLOBAL_IMAGE = img[:,:,:-1]
    GLOBAL_MASK = None
    GLOBAL_ZIPBUFFER = None
    # produce an image embedding by calling SamPredictor.set_image
    predictor.set_image(GLOBAL_IMAGE)
    print("finish setting image")
    # Return a JSON response
    return JSONResponse(
        content={
            "message": "Images received successfully",
        },
        status_code=200,
    )


@app.post("/undo")
async def undo_mask():
    global input_point, input_label, mask_input
    input_point.pop()
    input_label.pop()
    masks.pop()
    # mask_input.pop()

    return JSONResponse(
        content={
            "message": "Clear successfully",
        },
        status_code=200,
    )

@app.post("/click")
async def click_images(
    x: int = Form(...), # horizontal
    y: int = Form(...)  # vertical
):  
    global input_point, input_label, mask_input,GLOBAL_ORIGIN_IMAGE
    input_point.append([x, y])
    input_label.append(1)
    print("get click", x, y)
    print("input_point", input_point)
    print("input_label", input_label)

    
    masks_, scores_, logits_ = predictor.predict(
        point_coords=np.array([input_point[-1]]),
        point_labels=np.array([input_label[-1]]),
        # mask_input=mask_input[-1],
        multimask_output=True, # SAM outputs 3 masks, we choose the one with highest score
    )
    
    # mask_input.append(logits[np.argmax(scores), :, :][None, :, :])
    masks.append(masks_[np.argmax(scores_), :, :]) #the highest score mask added to the masks
    res = np.zeros(masks[0].shape)
    for mask in masks:
        res = np.logical_or(res, mask)
    # res = Image.fromarray(res)
    # change to the gray mode
    gray_res = (res * 255).astype(np.uint8)
    Image.fromarray(GLOBAL_ORIGIN_IMAGE).save("test.png")
    # cv2.imwrite("test.png", GLOBAL_ORIGIN_IMAGE)
    
    res = Image.fromarray(res)
    # gray_res = cv2.cvtColor(res, cv2.COLOR_BGR2GRAY) it will cause disorder of the color in the channels
    # res.save("res.png")
    # extract original part of the image by the mask
    masked_np = cv2.bitwise_and(GLOBAL_ORIGIN_IMAGE, GLOBAL_ORIGIN_IMAGE, mask=gray_res)
    Image.fromarray(masked_np).save("testmask.png")
    
    masked_image = Image.fromarray(masked_np)
    
    # Return a JSON response
    return JSONResponse(
        content={
            "masks": pil_image_to_base64(res),
            "masked_region": pil_image_to_base64(masked_image),
            "message": "Images processed successfully"
        },
        status_code=200,
    )

@app.post("/rect")
async def rect_images(
    start_x: int = Form(...), # horizontal
    start_y: int = Form(...), # vertical
    end_x: int = Form(...), # horizontal
    end_y: int = Form(...)  # vertical
):
    masks_, _, _ = predictor.predict(
        point_coords=None,
        point_labels=None,
        box=np.array([[start_x, start_y, end_x, end_y]]),
        multimask_output=False
    )
    
    res = Image.fromarray(masks_[0])
    print(masks_[0].shape)
    # res.save("res.png")

    # Return a JSON response
    return JSONResponse(
        content={
            "masks": pil_image_to_base64(res),
            "message": "Images processed successfully"
        },
        status_code=200,
    )

@app.post("/everything")
async def seg_everything():
    """
        segmentation : the mask
        area : the area of the mask in pixels
        bbox : the boundary box of the mask in XYWH format
        predicted_iou : the model's own prediction for the quality of the mask
        point_coords : the sampled input point that generated this mask
        stability_score : an additional measure of mask quality
        crop_box : the crop of the image used to generate this mask in XYWH format
    """
    global GLOBAL_IMAGE, GLOBAL_MASK, GLOBAL_ZIPBUFFER
    if type(GLOBAL_MASK) != type(None):
        return JSONResponse(
            content={
                "masks": pil_image_to_base64(GLOBAL_MASK),
                "zipfile": b64encode(GLOBAL_ZIPBUFFER.getvalue()).decode("utf-8"),
                "message": "Images processed successfully"
            },
            status_code=200,
        )


    masks = mask_generator.generate(GLOBAL_IMAGE)
    assert len(masks) > 0, "No masks found"

    sorted_anns = sorted(masks, key=(lambda x: x['area']), reverse=True)
    print(len(sorted_anns))

    # Create a new image with the same size as the original image
    img = np.zeros((sorted_anns[0]['segmentation'].shape[0], sorted_anns[0]['segmentation'].shape[1]), dtype=np.uint8)
    for idx, ann in enumerate(sorted_anns, 0):
        img[ann['segmentation']] = idx % 255 + 1 # color can only be in range [1, 255]
    
    res = Image.fromarray(img)
    GLOBAL_MASK = res

    # Save the original image, mask, and cropped segments to a zip file in memory
    zip_buffer = BytesIO()
    PIL_GLOBAL_IMAGE = Image.fromarray(GLOBAL_IMAGE)
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Cut out the segmented regions as smaller squares
        for idx, ann in enumerate(sorted_anns, 0):
            left, top, right, bottom = ann["bbox"][0], ann["bbox"][1], ann["bbox"][0] + ann["bbox"][2], ann["bbox"][1] + ann["bbox"][3]
            cropped = PIL_GLOBAL_IMAGE.crop((left, top, right, bottom))

            # Create a transparent image with the same size as the cropped image
            transparent = Image.new("RGBA", cropped.size, (0, 0, 0, 0))

            # Create a mask from the segmentation data and crop it
            mask = Image.fromarray(ann["segmentation"].astype(np.uint8) * 255)
            mask_cropped = mask.crop((left, top, right, bottom))

            # Combine the cropped image with the transparent image using the mask
            result = Image.composite(cropped.convert("RGBA"), transparent, mask_cropped)

            # Save the result to the zip file
            result_bytes = BytesIO()
            result.save(result_bytes, format="PNG")
            result_bytes.seek(0)
            zip_file.writestr(f"seg_{idx}.png", result_bytes.read())

    # move the file pointer to the beginning of the file so we can read whole file
    zip_buffer.seek(0)
    GLOBAL_ZIPBUFFER = zip_buffer

    # Return a JSON response
    return JSONResponse(
        content={
            "masks": pil_image_to_base64(GLOBAL_MASK),
            "zipfile": b64encode(GLOBAL_ZIPBUFFER.getvalue()).decode("utf-8"),
            "message": "Images processed successfully"
        },
        status_code=200,
    )

@app.get("/assets/{path}/{file_name}", response_class=FileResponse)
async def read_assets(path, file_name):
    return f"assets/{path}/{file_name}"

@app.get("/", response_class=HTMLResponse)
async def read_index():
    return read_content('segDrawer.html')

import uvicorn
uvicorn.run(app, host="0.0.0.0", port=8000)
