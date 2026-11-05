import os
import logging

from dotenv import load_dotenv


load_dotenv()


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")


if not GEMINI_API_KEY:
    logging.getLogger(__name__).warning(
        "GEMINI_API_KEY not found; Gemini functionality will be unavailable."
    )
