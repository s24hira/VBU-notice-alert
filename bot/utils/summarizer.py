import os
import base64
import requests
import json
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Literal
import logging

from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP

# Flatten all departments from the constants map
ALL_DEPARTMENTS = []
for depts in BHAVANA_DEPARTMENTS_MAP.values():
    ALL_DEPARTMENTS.extend(depts)


BHAVANA_ALIASES = {
    "siksha bhavan": "Siksha Bhavana",
    "siksha bhavana": "Siksha Bhavana",
    "palli siksha bhavana": "Palli Siksha Bhavana",
    "palli siksha bhavan": "Palli Siksha Bhavana",
    "vidya bhavan": "Vidya Bhavana",
    "vidya bhavana": "Vidya Bhavana",
    "bhasha bhavan": "Bhasha Bhavana",
    "bhasha bhavana": "Bhasha Bhavana",
    "kala bhavan": "Kala Bhavana",
    "kala bhavana": "Kala Bhavana",
    "sangit bhavan": "Sangit Bhavana",
    "sangit bhavana": "Sangit Bhavana",
    "vinaya bhavan": "Vinaya Bhavana",
    "vinaya bhavana": "Vinaya Bhavana",
    "psv": "PSV",
    "palli samgasa vibhaga": "PSV",
    "palli samgathan vibhag": "PSV",
    "palli samgathana vibhaga": "PSV",
    "schools & independent centres": "Schools & Independent Centres",
    "central administration / office": "Central Administration / Office",
    "central office": "Central Administration / Office"
}

DEPARTMENT_ALIASES = {
    "horticulture": "Horticulture & Post-Harvest Technology",
    "horticulture science": "Horticulture & Post-Harvest Technology",
    "vegetable science": "Horticulture & Post-Harvest Technology",
    "fruit science": "Horticulture & Post-Harvest Technology",
    "computer science": "Computer & System Sciences",
    "computer & system science": "Computer & System Sciences",
    "computer and system sciences": "Computer & System Sciences",
    "physics": "Physics",
    "chemistry": "Chemistry",
    "mathematics": "Mathematics",
    "zoology": "Zoology",
    "botany": "Botany",
    "statistics": "Statistics",
    "biotechnology": "Biotechnology",
    "environmental studies": "Environmental Studies",
    "iserc": "Integrated Science Education & Research Centre (ISERC)",
    "integrated science education & research centre": "Integrated Science Education & Research Centre (ISERC)",
    "economics": "Economics & Politics",
    "politics": "Economics & Politics",
    "economics & politics": "Economics & Politics",
    "history": "History",
    "aihca": "Ancient Indian History Culture & Archaeology (AIHCA)",
    "ancient indian history culture & archaeology": "Ancient Indian History Culture & Archaeology (AIHCA)",
    "anthropology": "Anthropology",
    "geography": "Geography",
    "philosophy": "Philosophy & Comparative Religion",
    "philosophy & comparative religion": "Philosophy & Comparative Religion",
    "cjmc": "Centre for Journalism & Mass Communication (CJMC)",
    "journalism": "Centre for Journalism & Mass Communication (CJMC)",
    "women's studies": "Centre for Women's Studies",
    "budhist studies": "Centre for Buddhist Studies",
    "bengali": "Bengali",
    "english": "English",
    "hindi": "Hindi",
    "sanskrit": "Sanskrit Pali & Prakrit",
    "odia": "Odia",
    "marathi": "Marathi",
    "santali": "Santali",
    "assamese": "Assamese",
    "tamil": "Tamil",
    "chinese": "Chinese Language & Culture",
    "japanese": "Japanese",
    "education": "Education",
    "physical education": "Physical Education & Sport Science",
    "yogic art": "Yogic Art & Science",
    "social work": "Social Work",
    "lifelong learning": "Lifelong Learning & Extension (REC)",
    "lifelong learning & extension": "Lifelong Learning & Extension (REC)",
    "rec": "Lifelong Learning & Extension (REC)",
    "rural studies": "Rural Studies (Palli Charcha Kendra / PCK)",
    "palli charcha kendra": "Rural Studies (Palli Charcha Kendra / PCK)",
    "silpa sadana": "Silpa-Sadana",
    "silpa-sadana": "Silpa-Sadana",
    "patha bhavana": "Patha Bhavana",
    "siksha satra": "Siksha Satra",
    "rabindra bhavana": "Rabindra Bhavana"
}



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

    @field_validator('target_bhavana', mode='before')
    @classmethod
    def validate_bhavana(cls, v):
        if not v or (isinstance(v, str) and v.lower() == 'null'):
            return None
        if not isinstance(v, str):
            return None
        
        v_clean = v.strip()
        v_lower = v_clean.lower()
        
        if v_lower in BHAVANA_ALIASES:
            return BHAVANA_ALIASES[v_lower]
            
        for b in BHAVANAS_LIST:
            if b.lower() == v_lower:
                return b
                
        logging.warning(f"Unrecognized Bhavana '{v}' from Gemini. Falling back to None.")
        return None

    @field_validator('target_department', mode='before')
    @classmethod
    def validate_department(cls, v):
        if not v or (isinstance(v, str) and v.lower() == 'null'):
            return None
        if not isinstance(v, str):
            return None
            
        v_clean = v.strip()
        v_lower = v_clean.lower()
        
        if v_lower in DEPARTMENT_ALIASES:
            return DEPARTMENT_ALIASES[v_lower]
            
        for d in ALL_DEPARTMENTS:
            if d.lower() == v_lower:
                return d
                
        logging.warning(f"Unrecognized Department '{v}' from Gemini. Falling back to None.")
        return None

    @model_validator(mode='after')
    def infer_bhavana_from_department(self):
        if self.target_department:
            # Find which Bhavana this department belongs to
            for bhavana, depts in BHAVANA_DEPARTMENTS_MAP.items():
                if self.target_department in depts:
                    if self.target_bhavana != bhavana:
                        logging.info(f"Overriding target_bhavana to '{bhavana}' based on target_department '{self.target_department}' (was '{self.target_bhavana}')")
                        self.target_bhavana = bhavana
                    break
        return self

class GeminiPDFSummarizer:
    def __init__(self, api_key):
        """
        Initialize Gemini PDF Summarizer
        """
        self.api_key = api_key
        self.model = 'gemini-3.5-flash'
        self.url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        self._headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key
        }

        # Pre-compute Gemini schema
        schema = NoticeExtraction.model_json_schema()
        self._gemini_schema = {
            "type": "OBJECT",
            "properties": {},
            "required": schema.get("required", [])
        }
        for prop_name, prop_details in schema.get("properties", {}).items():
            prop_type = prop_details.get("type", "STRING").upper()
            prop_schema = {
                "type": prop_type,
                "description": prop_details.get("description", "")
            }
            
            # Handle Pydantic v2 Union/Optional types which generate 'anyOf'
            if "anyOf" in prop_details:
                for sub_schema in prop_details["anyOf"]:
                    sub_type = sub_schema.get("type")
                    if sub_type and sub_type != "null":
                        prop_schema["type"] = sub_type.upper()
                        if "enum" in sub_schema:
                            prop_schema["enum"] = sub_schema["enum"]
                        break
                prop_schema["nullable"] = True
            elif "enum" in prop_details:
                prop_schema["enum"] = prop_details["enum"]
                
            self._gemini_schema["properties"][prop_name] = prop_schema

    def summarize_document(self, file_bytes, mime_type="application/pdf", max_retries=5, backoff_factor=5) -> NoticeExtraction:
        """
        Summarize a document (PDF or Image) and extract target audience parameters.
        Returns a NoticeExtraction pydantic object.
        """
        import time
        prompt = """
        Analyze the provided Visva-Bharati notice document (PDF or Image). The core vision for categorization is: "Does it impact the student, and if yes, which category (Bhavana/Department) is it impacting?"
        Extract the following information:
        1. A concise bullet-point summary in simple text format. DO NOT use markdown format (avoid * characters). DO NOT include helplines/links.
        2. target_bhavana: The exact Institute (Bhavana) name matching the allowed schema enum values. Map nicknames or variants (e.g. 'Siksha Bhavan' -> 'Siksha Bhavana', 'Palli Samgasa Vibhaga' -> 'PSV'). Null if not mentioned or doesn't match any allowed value.
           - IMPORTANT: If a notice is issued by the central office but involves actions/interests specific to a particular Institute/Department's students, set target_bhavana to that specific Institute, NOT the Central Office.
        3. target_department: The exact Department name matching the allowed schema enum values. Map variants (e.g. 'Department of Physics' -> 'Physics', 'Dept of CS' -> 'Computer & System Sciences'). Null if not mentioned or doesn't match any allowed value.
           - IMPORTANT: For joining notices, you MUST identify and set the target_department (and target_bhavana if applicable) for which the employee is joining.
        4. is_general: Set to true if this notice applies broadly to all students/staff, or false if it is specific to particular institutes/departments.
           - IMPORTANT: If a notice is issued by the central office but involves actions/interests for ALL university students, MUST set is_general to true.
           - IMPORTANT: For joining notices, DO NOT classify it as a general notice (is_general MUST be false), and ensure the specific department is identified.
        """

        b64_data = base64.b64encode(file_bytes).decode('utf-8')
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64_data
                            }
                        },
                        {
                            "text": prompt
                        }
                    ]
                }
            ],
            "generationConfig": {
                "response_mime_type": "application/json",
                "response_schema": self._gemini_schema
            }
        }
        del b64_data  # Free memory immediately

        try:
            for attempt in range(max_retries):
                try:
                    logging.info(f"Generating summary and categorization from in-memory document (attempt {attempt + 1}/{max_retries})...")
                    with requests.post(self.url, headers=self._headers, json=payload) as response:
                        response.raise_for_status()
                        
                        response_data = response.json()
                    text_result = response_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                    
                    if not text_result:
                        raise SummarizationError("Empty response from Gemini API.")
                        
                    return NoticeExtraction.model_validate_json(text_result)

                except Exception as e:
                    logging.error(f"Error in Gemini summarization (attempt {attempt + 1}/{max_retries}): {e}")
                    if attempt < max_retries - 1:
                        sleep_time = backoff_factor * (2 ** attempt)
                        logging.info(f"Retrying in {sleep_time} seconds...")
                        time.sleep(sleep_time)
                    else:
                        raise SummarizationError(f"Failed to generate structured summary from Gemini after {max_retries} attempts.")

            raise SummarizationError("An unexpected error occurred during document processing.")
        finally:
            del payload
