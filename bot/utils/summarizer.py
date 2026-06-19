import os
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
import logging

from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP

# Flatten all departments from the constants map
ALL_DEPARTMENTS = []
for depts in BHAVANA_DEPARTMENTS_MAP.values():
    ALL_DEPARTMENTS.extend(depts)


BhavanaType = Literal[
    "Palli Siksha Bhavana",
    "Siksha Bhavana",
    "Vidya Bhavana",
    "Bhasha Bhavana",
    "Kala Bhavana",
    "Sangit Bhavana",
    "Vinaya Bhavana",
    "PSV",
    "Schools & Independent Centres",
    "Central Administration / Office"
]

DepartmentType = Literal[
    # Palli Siksha Bhavana
    "Agronomy", "Plant Pathology", "Agricultural Entomology", 
    "Agricultural Economics", "Agricultural Extension", 
    "Agricultural Statistics", "Soil Science & Agricultural Chemistry", 
    "Horticulture & Post-Harvest Technology", "Genetics & Plant Breeding", 
    "Crop Physiology", "Agricultural Engineering", "Animal Science",
    # Siksha Bhavana
    "Mathematics", "Physics", "Chemistry", "Botany", "Zoology", 
    "Computer & System Sciences", "Statistics", "Environmental Studies", 
    "Biotechnology", "Integrated Science Education & Research Centre (ISERC)",
    # Vidya Bhavana
    "Economics & Politics", "History", 
    "Ancient Indian History Culture & Archaeology (AIHCA)", 
    "Anthropology", "Geography", "Philosophy & Comparative Religion", 
    "Centre for Journalism & Mass Communication (CJMC)", 
    "Centre for Women's Studies", "Centre for Buddhist Studies",
    # Bhasha Bhavana
    "Bengali", "English", "Hindi", "Sanskrit Pali & Prakrit", 
    "Odia", "Marathi", "Santali", "Assamese", "Tamil", 
    "Indo-Tibetan Studies", "Chinese Language & Culture", "Japanese", 
    "Arabic Persian Urdu & Islamic Studies", 
    "Centre for Modern European Languages Literatures & Culture Studies (CMELLCS)", 
    "Centre for Comparative Literature", "Centre for Endangered Languages",
    # Kala Bhavana
    "History of Art", "Painting", "Sculpture", "Graphic Art (Printmaking)", 
    "Ceramic & Glass Design", "Textile Design",
    # Sangit Bhavana
    "Hindustani Classical Music", "Rabindra Sangit Dance & Drama (RSDD)",
    # Vinaya Bhavana
    "Education", "Physical Education & Sport Science", "Yogic Art & Science",
    # PSV
    "Social Work", "Lifelong Learning & Extension (REC)", 
    "Rural Studies (Palli Charcha Kendra / PCK)", "Silpa-Sadana",
    # Schools & Independent Centres
    "Patha Bhavana", "Siksha Satra", "Rabindra Bhavana", "Granthana Vibhaga", 
    "Rathindra Krishi Vigyan Kendra", "A.K. Dasgupta Centre for Planning & Development",
    # Central Administration / Office
    "Central Office", "Academic & Research", "Examination", "Establishment", 
    "Accounts", "Estate", "Security", "Library", "Public Relations", 
    "Engineering", "Health Centre"
]

# Define a custom exception for summarization errors
class SummarizationError(Exception):
    pass

class NoticeExtraction(BaseModel):
    summary: str = Field(description="A concise bullet-point summary. Only include key actionable points. NO markdown asterisks.")

    target_bhavana: Optional[BhavanaType] = Field(description="Targeted Institute (Bhavana) name if specified, otherwise null.")
    target_department: Optional[DepartmentType] = Field(description="Targeted Department name if specified, otherwise null.")
    is_general: bool = Field(description="True if the notice applies to all students/general audience, False if it targets specific levels/bhavanas.")

class GeminiPDFSummarizer:
    def __init__(self, api_key):
        """
        Initialize Gemini PDF Summarizer
        """
        self.client = genai.Client(api_key=api_key)
        self.model = 'gemini-3.1-flash-lite'

    def summarize_pdf(self, pdf_bytes, max_retries=3, backoff_factor=5) -> NoticeExtraction:
        """
        Summarize a PDF and extract target audience parameters.
        Returns a NoticeExtraction pydantic object.
        """
        import time
        prompt = """
        Analyze the provided Visva-Bharati notice PDF.
        Extract the following information:
        1. A concise bullet-point summary in simple text format. DO NOT use markdown format (avoid * characters). DO NOT include helplines/links.
        2. target_bhavana: The exact Institute (Bhavana) name matching the allowed schema enum values. Map nicknames or variants (e.g. 'Siksha Bhavan' -> 'Siksha Bhavana', 'Palli Samgasa Vibhaga' -> 'PSV'). Null if not mentioned or doesn't match any allowed value.
           - IMPORTANT: If a notice is issued by the central office but involves actions/interests specific to a particular Institute/Department's students, set target_bhavana to that specific Institute, NOT the Central Office.
        3. target_department: The exact Department name matching the allowed schema enum values. Map variants (e.g. 'Department of Physics' -> 'Physics', 'Dept of CS' -> 'Computer & System Sciences'). Null if not mentioned or doesn't match any allowed value.
        4. is_general: Set to true if this notice applies broadly to all students/staff, or false if it is specific to particular institutes/departments.
           - IMPORTANT: If a notice is issued by the central office but involves actions/interests for ALL university students, MUST set is_general to true.
        """

        for attempt in range(max_retries):
            try:
                logging.info(f"Generating summary and categorization from in-memory PDF content (attempt {attempt + 1}/{max_retries})...")
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
                if hasattr(response, 'parsed') and response.parsed:
                    return response.parsed
                
                # Fallback if parsed is not populated for some reason
                return NoticeExtraction.model_validate_json(response.text)

            except Exception as e:
                logging.error(f"Error in Gemini summarization (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    sleep_time = backoff_factor * (2 ** attempt)
                    logging.info(f"Retrying in {sleep_time} seconds...")
                    time.sleep(sleep_time)
                else:
                    raise SummarizationError(f"Failed to generate structured summary from Gemini after {max_retries} attempts.")

        raise SummarizationError("An unexpected error occurred during PDF processing.")
