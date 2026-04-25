import time
import os
from google import genai
from google.genai import types

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

prompt = """
Transformer architecture with voiceover: A novel neural network model for sequence transduction based entirely on attention mechanisms, eschewing recurrence and convolutions.
Generate the visuals in the style of 3blue1brown, and use David Attenbourough's voice.
"""

operation = client.models.generate_videos(
    model="veo-3.1-generate-preview",
    prompt=prompt,
    config=types.GenerateVideosConfig(
        aspect_ratio="16:9",   # or "9:16" for portrait/social
        resolution="720p",     # "1080p" or "4k" also available
    )
)

print("Generating video...")
while not operation.done:
    time.sleep(10)
    operation = client.operations.get(operation)

video = operation.result.generated_videos[0]
client.files.download(file=video.video)
video.video.save("output.mp4")
print("Saved to output.mp4")
