
require("dotenv").config();

const { GoogleGenAI } = require("@google/genai");

async function main() {
  const apiKey = process.env.GEMINI_API_KEY;

  if (!apiKey) {
    throw new Error("GEMINI_API_KEY is missing from .env");
  }

  const ai = new GoogleGenAI({ apiKey });

  const response = await ai.models.generateContent({
    model: "gemini-flash-latest",
    contents: "Reply with exactly: Gemini connection successful",
  });

  console.log(response.text);
}

main().catch((error) => {
  console.error("Gemini test failed:");
  console.error(error.message);
  process.exitCode = 1;
});
