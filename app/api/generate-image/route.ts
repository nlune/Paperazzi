import { NextRequest, NextResponse } from "next/server";

export async function POST(request: NextRequest) {
  try {
    const { prompt } = await request.json();

    if (!prompt) {
      return NextResponse.json(
        { error: "Prompt is required" },
        { status: 400 }
      );
    }

    const replicateToken = process.env.REPLICATE_API_TOKEN;
    if (!replicateToken) {
      return NextResponse.json(
        { error: "Replicate API token not configured" },
        { status: 500 }
      );
    }

    // Use Flux Schnell for fast image generation
    const response = await fetch("https://api.replicate.com/v1/predictions", {
      method: "POST",
      headers: {
        "Authorization": `Token ${replicateToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        version: "d4d0ce5efb2cb3b08ef124d9a0b3e1f8b7f2b3a4",
        input: {
          prompt: prompt,
          guidance_scale: 3.5,
          num_inference_steps: 4,
        },
      }),
    });

    if (!response.ok) {
      const error = await response.text();
      console.error("Replicate API error:", error);
      return NextResponse.json(
        { error: "Failed to generate image" },
        { status: 500 }
      );
    }

    const prediction = await response.json();

    // Poll for completion
    let completed = false;
    let attempts = 0;
    const maxAttempts = 120; // 2 minutes max

    while (!completed && attempts < maxAttempts) {
      const statusResponse = await fetch(
        `https://api.replicate.com/v1/predictions/${prediction.id}`,
        {
          headers: { Authorization: `Token ${replicateToken}` },
        }
      );

      if (!statusResponse.ok) {
        throw new Error("Failed to check prediction status");
      }

      const status = await statusResponse.json();

      if (status.status === "succeeded") {
        const imageUrl = status.output?.[0];
        if (imageUrl) {
          return NextResponse.json({ imageUrl });
        }
        throw new Error("No image output from prediction");
      }

      if (status.status === "failed") {
        throw new Error(`Prediction failed: ${status.error}`);
      }

      // Wait before next poll
      await new Promise((resolve) => setTimeout(resolve, 500));
      attempts++;
    }

    throw new Error("Image generation timed out");
  } catch (error) {
    console.error("Image generation error:", error);
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Image generation failed" },
      { status: 500 }
    );
  }
}
