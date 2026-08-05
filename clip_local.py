"""
Локальний розрахунок CLIP-ембедінгів для пошуку запчастин по фото.

Модель завантажується один раз при першому виклику (лінива ініціалізація)
і кешується в пам'яті процесу. Жодних зовнішніх API чи токенів не потрібно —
все рахується на CPU/GPU самого сервера бота.

Встановлення залежностей:
    pip install sentence-transformers torch pillow

Модель 'clip-ViT-B-32' (~600 МБ) завантажиться автоматично при першому
запуску і закешується в ~/.cache/huggingface — інтернет потрібен лише
одноразово, для наступних запусків працює офлайн.
"""

import asyncio
import logging
import threading
from io import BytesIO

from PIL import Image

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()

MODEL_NAME = "clip-ViT-B-32"


def _load_model():
    """Лінива ініціалізація моделі — вантажиться один раз при першому фото."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Завантаження локальної CLIP-моделі ({MODEL_NAME})...")
                _model = SentenceTransformer(MODEL_NAME)
                logger.info("CLIP-модель завантажена та готова.")
    return _model


def get_clip_embedding_sync(image_bytes: bytes) -> list | None:
    """
    Синхронно рахує CLIP-ембедінг зображення.
    Викликається у окремому потоці через get_clip_embedding(), щоб не
    блокувати event loop бота під час інференсу на CPU.
    """
    try:
        model = _load_model()
        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        embedding = model.encode(image, convert_to_numpy=True)
        return embedding.tolist()
    except Exception as e:
        logger.error(f"CLIP embedding error: {e}")
        return None


async def get_clip_embedding(image_bytes: bytes) -> list | None:
    """
    Асинхронна обгортка над get_clip_embedding_sync().
    Використовує asyncio.to_thread, щоб важкий CPU-інференс не блокував
    обробку інших повідомлень бота.
    """
    return await asyncio.to_thread(get_clip_embedding_sync, image_bytes)
