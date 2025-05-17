import re
import os
import pandas as pd
from typing import List, Dict, Any
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from .utils import load_schema
from core.config.settings import get_settings
from core.config.paths import path_config
from core.logging.logger import get_logger, log_execution
from domain.exceptions.custom import CodeGenerationError

logger = get_logger(__name__)

class CodeGenerator:
    def __init__(self):
        settings = get_settings()
        self.response_dir = path_config.RESPONSE_DIR
        self.stats_dir = path_config.STATS_DIR
        self.graphs_dir = path_config.GRAPHS_DIR
        self.schema= load_schema()
        
        # Explicitly set the API key in environment
        os.environ["GOOGLE_API_KEY"] = settings.GOOGLE_API_KEY
        
        self.llm = ChatGoogleGenerativeAI(
            model=settings.GEMINI_MODEL_NAME,
            google_api_key=settings.GOOGLE_API_KEY,
            convert_system_message_to_human=True,
            **settings.MODEL_SETTINGS
        )
        self._setup_prompt_template()
    
    def _setup_prompt_template(self):
        """Setup the code generation prompt template"""
        self.code_prompt = ChatPromptTemplate.from_messages([
            ("system", f"""Generate data visualization and statistical analysis code using these imports:

import os, json, pandas as pd, numpy as np, matplotlib.pyplot as plt, seaborn as sns
sns.set()
plt.ioff()

RESPONSE_DIR = r"{self.response_dir}"
STATS_DIR = r"{self.stats_dir}"
GRAPHS_DIR = r"{self.graphs_dir}"

Please follow the correct JSON format for saving stats, as provided in template

Remember to:
1. I am Provding data_types of column, read that and perform appropriate analysis
2. Convert int32, int64, or float64 to Python’s built-in int, which is JSON serializable
2. Use Numpy to Perform all Statistical Analysis
3. Always use this exact JSON structure and handle proper JSON serialization
4. Dont Perform unnecessary statistics analysis but keep only basic mean, meadian, mode for every column involved in questions 
5. Add relevant metrics to additional_metrics if needed
6. Format numbers appropriately (round to 4 decimal places)
7. Use os.path.join for file paths
8. Include proper error handling
9. Start Question numbering from 1

File naming:
- You MUST include the base_name variable and set it to a descriptive name for your analysis
- One graph (.png) and one stats (_stats.json) file per question
- Use descriptive names: <metric>_<analysis_type>
- Define filenames at start:
  base_name = "metric_analysis"
  graph_file = os.path.join(GRAPHS_DIR,base_name.png")
  stats_file = os.path.join(STATS_DIR, base_name_stats.json")

Remember to add the Question in the returned stats JSON file  
  
Data preprocessing:
- Drop irrelevant columns
- Handle missing values
- Convert data types
- Remove duplicates
- Handle outliers

Visualization:
- Use appropriate plot types
- Include title, labels, legend, and grid
- Enhance readability

Validation:
- Check data format and sufficiency
- Verify figure creation and readability
- Validate file paths and permissions

Input handling:
df = pd.read_csv(data_path)
if df.empty:
    raise ValueError("Empty dataframe")

Remember to close plots and clear memory when done."""),
            ("human", """Create visualization code for:
            Columns: {columns}
            Data: {head_data}
            Task: {question}
            Path: {data_path}
            """)
        ])
        self.chain = self.code_prompt | self.llm

    @log_execution
    def remove_code_block_formatting(self, code: str) -> str:
        """Clean code formatting"""
        try:
            cleaned = re.sub(r'```python\s*|\s*```', '', code)
            if match := re.search(r'exec\("""(.*?)"""\)', cleaned, re.DOTALL):
                return match.group(1).strip()
            return cleaned
        except Exception as e:
            raise CodeGenerationError(str(e))

    @log_execution
    def generate_code_for_question(self, question: str, columns: List[str], head_data: pd.DataFrame, data_path: str,d_types, schema) -> tuple[str, str]:
        """Generate code with schema example"""
        try:   
            response = self.chain.invoke({
                "columns": columns,
                "head_data": head_data,
                "question": question,
                "data_path": data_path,
                "data_type": d_types,
                "schema": schema
            })
            
            base_code = self.remove_code_block_formatting(response.content)
            
            filename_match = re.search(r'base_name\s*=\s*["\']([^"\']+)["\']', base_code)
            if not filename_match:
                raise CodeGenerationError("Could not find base_name in generated code")
            
            filename = f"{filename_match.group(1)}.png"
            
            # Include question in stats output
            stats_save_pattern = r'json\.dump\((.*?),\s*f,\s*default=convert_to_serializable,\s*indent=4\)'
            modified_code = re.sub(
                stats_save_pattern,
                rf'json.dump({{\n    "question": """{question}""",\n    "analysis": \1}}, f, default=convert_to_serializable, indent=4)',
                base_code
            )
            
            return modified_code, filename
        except Exception as e:
            raise CodeGenerationError(f"Failed to generate code: {str(e)}")
    

    @log_execution
    def save_generated_code(self, code: str) -> str:
        """Save generated code to file"""
        try:
            code_path = path_config.CODE_DIR / "generated_analysis_code.py"
            with open(code_path, 'w') as f:
                f.write(code)
            return str(code_path)
        except Exception as e:
            raise CodeGenerationError(f"Failed to save code: {str(e)}")

    @log_execution
    def generate(self, provided_questions: List[str] = None) -> Dict[str, Any]:
        """Main generation method"""
        try:
            if not os.environ.get("DATA_FILE_PATH"):
                raise ValueError("No data file path provided")

            data_path = os.environ["DATA_FILE_PATH"]
            df = pd.read_csv(data_path) 
            columns = df.columns.tolist()
            head_data = df.head()
            d_types = d_types = df.dtypes.apply(lambda x: str(x)).to_dict()
            schema= self.schema
            
            generated_code = ""
            filenames = []
            
            for i, question in enumerate(provided_questions or []):
                code, filename = self.generate_code_for_question(
                    question, columns, head_data, data_path, d_types, schema
                )
                generated_code += f"# Question {i}: {question}\n# Output: {filename}\n{code}\n\n"
                filenames.append(filename)
                
            code_path = self.save_generated_code(generated_code)
        
            return {
                "code": generated_code,
                "filenames": filenames,
                "code_path": code_path,
                "status": "success"
            }
        except Exception as e:
            logger.error(f"Code generation failed: {str(e)}")
            raise CodeGenerationError(str(e))