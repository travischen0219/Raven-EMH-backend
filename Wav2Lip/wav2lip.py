# %%
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MODULE_ROOT = ROOT / "Wav2Lip"
sys.path.append(str(MODULE_ROOT))
import platform
import subprocess
import time

import audio
import cv2
import face_detection
import numpy as np
import torch
from loguru import logger
from tqdm import tqdm
from wav2lip_models import Wav2Lip

from configs import paths
from configs import paths as paths

start = time.time()


class Wav2LipAAG:
    def __init__(self):
        self.checkpoint_path = str(MODULE_ROOT / "checkpoints/wav2lip.pth")
        self.face = str(paths.DATA / "avatar.png")
        self.outfile = str(paths.DATA / "output.mp4")
        self.box = [-1, -1, -1, -1]
        self.crop = [0, -1, 0, -1]
        self.face_det_batch_size = 64
        self.fps = 25.0
        self.img_size = 96
        self.nosmooth = False
        self.pads = [0, 10, 0, 0]
        self.resize_factor = 1
        self.rotate = False
        self.static = True
        self.wav2lip_batch_size = 256
        self.img_size = 96
        self.static = True
        self.mel_step_size = 16
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Using {} for inference.".format(self.device))

        self.model = self.load_model(self.checkpoint_path)
        self.full_frames = [cv2.imread(self.face)]

    def get_smoothened_boxes(self, boxes, T):
        for i in range(len(boxes)):
            if i + T > len(boxes):
                window = boxes[len(boxes) - T :]
            else:
                window = boxes[i : i + T]
            boxes[i] = np.mean(window, axis=0)
        return boxes

    def face_detect(self, images):
        detector = face_detection.FaceAlignment(
            face_detection.LandmarksType._2D, flip_input=False, device=self.device
        )

        batch_size = self.face_det_batch_size

        while 1:
            predictions = []
            try:
                for i in tqdm(range(0, len(images), batch_size)):
                    predictions.extend(
                        detector.get_detections_for_batch(
                            np.array(images[i : i + batch_size])
                        )
                    )
            except RuntimeError:
                if batch_size == 1:
                    raise RuntimeError(
                        "Image too big to run face detection on GPU. Please use the --resize_factor argument"
                    )
                batch_size //= 2
                logger.info(
                    "Recovering from OOM error; New batch size: {}".format(batch_size)
                )
                continue
            break

        results = []
        pady1, pady2, padx1, padx2 = self.pads
        for rect, image in zip(predictions, images):
            if rect is None:
                cv2.imwrite(
                    str(MODULE_ROOT / "temp/faulty_frame.jpg"), image
                )  # check this frame where the face was not detected.
                raise ValueError(
                    "Face not detected! Ensure the video contains a face in all the frames."
                )

            y1 = max(0, rect[1] - pady1)
            y2 = min(image.shape[0], rect[3] + pady2)
            x1 = max(0, rect[0] - padx1)
            x2 = min(image.shape[1], rect[2] + padx2)

            results.append([x1, y1, x2, y2])

        boxes = np.array(results)
        if not self.nosmooth:
            boxes = self.get_smoothened_boxes(boxes, T=5)
        results = [
            [image[y1:y2, x1:x2], (y1, y2, x1, x2)]
            for image, (x1, y1, x2, y2) in zip(images, boxes)
        ]

        del detector
        return results

    def datagen(self, frames, mels):
        img_batch, mel_batch, frame_batch, coords_batch = [], [], [], []

        if self.box[0] == -1:
            if not self.static:
                face_det_results = self.face_detect(
                    frames
                )  # BGR2RGB for CNN face detection
            else:
                face_det_results = self.face_detect([frames[0]])
        else:
            logger.info("Using the specified bounding box instead of face detection...")
            y1, y2, x1, x2 = self.box
            face_det_results = [[f[y1:y2, x1:x2], (y1, y2, x1, x2)] for f in frames]

        for i, m in enumerate(mels):
            idx = 0 if self.static else i % len(frames)
            frame_to_save = frames[idx].copy()
            face, coords = face_det_results[idx].copy()

            face = cv2.resize(face, (self.img_size, self.img_size))

            img_batch.append(face)
            mel_batch.append(m)
            frame_batch.append(frame_to_save)
            coords_batch.append(coords)

            if len(img_batch) >= self.wav2lip_batch_size:
                img_batch, mel_batch = np.asarray(img_batch), np.asarray(mel_batch)

                img_masked = img_batch.copy()
                img_masked[:, self.img_size // 2 :] = 0

                img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.0
                mel_batch = np.reshape(
                    mel_batch,
                    [len(mel_batch), mel_batch.shape[1], mel_batch.shape[2], 1],
                )

                yield img_batch, mel_batch, frame_batch, coords_batch
                img_batch, mel_batch, frame_batch, coords_batch = [], [], [], []

        if len(img_batch) > 0:
            img_batch, mel_batch = np.asarray(img_batch), np.asarray(mel_batch)

            img_masked = img_batch.copy()
            img_masked[:, self.img_size // 2 :] = 0

            img_batch = np.concatenate((img_masked, img_batch), axis=3) / 255.0
            mel_batch = np.reshape(
                mel_batch, [len(mel_batch), mel_batch.shape[1], mel_batch.shape[2], 1]
            )

            yield img_batch, mel_batch, frame_batch, coords_batch

    def _load(self, checkpoint_path):
        if self.device == "cuda":
            checkpoint = torch.load(checkpoint_path)
        else:
            checkpoint = torch.load(
                checkpoint_path, map_location=lambda storage, loc: storage
            )
        return checkpoint

    def load_model(self, path):
        tmp = time.time()
        model = Wav2Lip()
        logger.info("Load checkpoint from: {}".format(path))
        checkpoint = self._load(path)
        s = checkpoint["state_dict"]
        new_s = {}
        for k, v in s.items():
            new_s[k.replace("module.", "")] = v
        model.load_state_dict(new_s)

        model = model.to(self.device)
        logger.info(f"model loaded in {time.time()-tmp}")
        return model.eval()

    def generate_avatar(self, audio_fname: str):
        tmp = time.time()
        wav = audio.load_wav(audio_fname, 16000)
        mel = audio.melspectrogram(wav)

        mel_chunks = []
        mel_idx_multiplier = 80.0 / self.fps
        i = 0
        while 1:
            start_idx = int(i * mel_idx_multiplier)
            if start_idx + self.mel_step_size > len(mel[0]):
                mel_chunks.append(mel[:, len(mel[0]) - self.mel_step_size :])
                break
            mel_chunks.append(mel[:, start_idx : start_idx + self.mel_step_size])
            i += 1

        logger.info(f"mel chunks loaded in {time.time()-tmp}")
        logger.info("Length of mel chunks: {}".format(len(mel_chunks)))

        full_frames = self.full_frames[: len(mel_chunks)]

        batch_size = self.wav2lip_batch_size
        gen = self.datagen(full_frames.copy(), mel_chunks)

        logger.info("generated data")

        for i, (img_batch, mel_batch, frames, coords) in enumerate(
            tqdm(
                gen,
                total=int(np.ceil(float(len(mel_chunks)) / batch_size)),
                desc="Running inference",
            )
        ):
            if i == 0:
                frame_h, frame_w = full_frames[0].shape[:-1]
                out = cv2.VideoWriter(
                    str(MODULE_ROOT / "temp/result.avi"),
                    cv2.VideoWriter_fourcc(*"DIVX"),
                    self.fps,
                    (frame_w, frame_h),
                )

            img_batch = torch.FloatTensor(np.transpose(img_batch, (0, 3, 1, 2))).to(
                self.device
            )
            mel_batch = torch.FloatTensor(np.transpose(mel_batch, (0, 3, 1, 2))).to(
                self.device
            )

            with torch.no_grad():
                pred = self.model(mel_batch, img_batch)

            pred = pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0

            for p, f, c in zip(pred, frames, coords):
                y1, y2, x1, x2 = c
                p = cv2.resize(p.astype(np.uint8), (x2 - x1, y2 - y1))

                f[y1:y2, x1:x2] = p
                out.write(f)

        logger.info(f"generated video in {time.time()-tmp}")

        out.release()

        logger.info("Output video saved at {}".format(self.outfile))
        command = "ffmpeg -y -i {} -i {} -strict -2 -q:v 1 {}".format(
            audio_fname, str(MODULE_ROOT / "temp/result.avi"), self.outfile
        )
        logger.info("Merging audio and video using: {}".format(command))
        subprocess.call(
            command,
            shell=platform.system() != "Windows",
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Audio Video generated in {}".format(self.outfile))

        return self.outfile
