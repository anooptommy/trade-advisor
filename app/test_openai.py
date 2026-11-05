import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client=OpenAI(
        api_key=os.getenv("OPENAI_API_KEY")
)

response = client.responses.create(
        model="YOUR_MODEL",
        input="Reply with exactly : Trading agent connection successful."
)

print(response.output_text)

