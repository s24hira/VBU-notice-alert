import os
from google import genai
from google.genai import types
import logging
import time

# Define a custom exception for summarization errors
class SummarizationError(Exception):
    pass

class GeminiPDFSummarizer:
    def __init__(self, api_key):
        """
        Initialize Gemini PDF Summarizer

        Args:
            api_key (str): Google Gemini API key
        """
        # Initialize Gemini Client
        self.client = genai.Client(api_key=api_key)
        self.model = 'gemini-3-flash-preview'

    def summarize_pdf(self, pdf_bytes):
        """
        Summarize a PDF using Gemini API from in-memory bytes.

        Args:
            pdf_bytes (bytes): The raw bytes of the PDF document

        Returns:
            str: Summary of the PDF

        Raises:
            SummarizationError: If summarization fails
        """
        try:
            prompt = """
            Extract a concise bullet-point summary from the provided document in simple text format, strictly avoid markdown format as this introduces * in between the message.
            The summary should only include key information that is directly relevant and important for candidates.
            DO NOT include helpline, contact information, website link etc. Focus on critical updates, dates, requirements, instructions, and other actionable points.
            Ensure each point is brief and clear, targeting the needs of exam candidates. Provide enough empty space between lines.
            """

            try:
                logging.info("Generating summary from in-memory PDF content...")
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[
                        types.Part.from_bytes(
                            data=pdf_bytes,
                            mime_type="application/pdf",
                        ),
                        prompt
                    ]
                )
                summary = response.text
                return summary

            except Exception as e:
                logging.error(f"Error in Gemini summarization: {e}")
                raise SummarizationError("Failed to generate summary from Gemini.")

        except Exception as e:
            logging.error(f"Error processing PDF: {e}")
            raise SummarizationError("An unexpected error occurred during PDF processing.")
