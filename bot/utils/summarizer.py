import os
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Optional
import logging

# Define a custom exception for summarization errors
class SummarizationError(Exception):
    pass

class NoticeExtraction(BaseModel):
    summary: str = Field(description="A concise bullet-point summary. Only include key actionable points. NO markdown asterisks.")
    target_levels: Optional[List[str]] = Field(description="List of targeted academic levels (e.g. 'UG', 'PG', 'Ph.D. & Research', 'Certificate/Diploma'). Empty if general.")
    target_bhavana: Optional[str] = Field(description="Targeted Institute (Bhavana) name if specified, otherwise null.")
    target_department: Optional[str] = Field(description="Targeted Department name if specified, otherwise null.")
    is_general: bool = Field(description="True if the notice applies to all students/general audience, False if it targets specific levels/bhavanas.")

class GeminiPDFSummarizer:
    def __init__(self, api_key):
        """
        Initialize Gemini PDF Summarizer
        """
        self.client = genai.Client(api_key=api_key)
        self.model = 'gemini-3-flash-preview'

    def summarize_pdf(self, pdf_bytes) -> NoticeExtraction:
        """
        Summarize a PDF and extract target audience parameters.
        Returns a NoticeExtraction pydantic object.
        """
        try:
            prompt = """
            Analyze the provided Visva-Bharati notice PDF.
            Extract the following information:
            1. A concise bullet-point summary in simple text format. DO NOT use markdown format (avoid * characters). DO NOT include helplines/links.
            2. target_levels: Determine if this applies to 'UG', 'PG', 'Ph.D. & Research', or 'Certificate/Diploma'. Return a list of matching levels, or empty if it applies to everyone or isn't specified.
            3. target_bhavana: The exact Institute (Bhavana) name if specified (e.g., 'Siksha Bhavana', 'Vidya Bhavana', 'Palli Siksha Bhavana'). Null if not mentioned.
            4. target_department: The exact Department name if specified (e.g., 'Computer & System Sciences', 'Agronomy'). Null if not mentioned.
            5. is_general: Set to true if this notice applies broadly to all students/staff, or false if it is specific to particular levels/institutes/departments.
            """

            try:
                logging.info("Generating summary and categorization from in-memory PDF content...")
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=[
                        types.Part.from_bytes(
                            data=pdf_bytes,
                            mime_type="application/pdf",
                        ),
                        prompt
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=NoticeExtraction,
                    ),
                )
                
                # The returned text is JSON string matching the schema. Pydantic can parse it.
                # Actually, in google-genai 2.8.0, response.parsed contains the Pydantic object if schema is provided.
                if hasattr(response, 'parsed') and response.parsed:
                    return response.parsed
                
                # Fallback if parsed is not populated for some reason
                return NoticeExtraction.model_validate_json(response.text)

            except Exception as e:
                logging.error(f"Error in Gemini summarization: {e}")
                raise SummarizationError("Failed to generate structured summary from Gemini.")

        except Exception as e:
            logging.error(f"Error processing PDF: {e}")
            raise SummarizationError("An unexpected error occurred during PDF processing.")
