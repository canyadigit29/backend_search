import json
import re
import time
import io
import os
from typing import Dict, Optional, List
from PIL import Image
from pathlib import Path
from langchain_google_genai import GoogleGenerativeAI
from langchain.schema.messages import HumanMessage
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from core.config.settings import get_settings
from core.config.paths import path_config
from core.logging.logger import get_logger, log_execution
from domain.exceptions.custom import DataProcessingError

logger = get_logger(__name__)

class DescriptionGenerator:
    def __init__(self, batch_size: int = 1, min_delay: float = 3.0):
        try:
            settings = get_settings()
            
            # Explicitly set the API key in environment
            os.environ["GOOGLE_API_KEY"] = settings.GOOGLE_API_KEY
            
            self.llm = GoogleGenerativeAI(
                model=settings.GEMINI_MODEL_NAME,
                google_api_key=settings.GOOGLE_API_KEY, 
                **settings.MODEL_SETTINGS
            )
            self._setup_parameters(batch_size, min_delay)
            self._setup_analysis_template()
            
            logger.info("DescriptionGenerator initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize DescriptionGenerator: {str(e)}")
            raise DataProcessingError(str(e))
    
    def _setup_parameters(self, batch_size: int, min_delay: float):
        """Set up processing parameters"""
        self.batch_size = batch_size
        self.min_delay = min_delay
        self.last_api_call = 0
        
        # Image optimization parameters
        self.max_image_size = (500, 500)
        self.image_quality = 50
        self.max_image_size_kb = 100
    
    def _setup_analysis_template(self):
        """Set up the analysis template"""
        self.analysis_template = """Analyze this visualization and statistical data to provide a comprehensive professional analysis. Consider ALL aspects of the data:

1. All the Statistics Related to features involved in graph are provided in the prompt.
2. Try to answer the analysis question asked, with the help of provided statistics and visually analyzing graph.
3. Use the statistical data provided to support your analysis.
4. Analyze the provided data as if you are a Professional Data Scientist, try to cover all aspects.        

Format your response in the following JSON structure:

{
    "sections": [
        {
            "title": "Clear and Professional Title based on Analysis",
            "heading": "Analysis Overview",
            "content": "Comprehensive answer incorporating statistical findings",
            "data_points": [
                {
                    "metric": "Statistical measure name",
                    "value": "Numerical or categorical result",
                    "significance": "Business and statistical importance"
                }
            ]
        },
        {
            "heading": "Statistical Evidence",
            "content": "Detailed statistical interpretation",
            "calculations": [
                {
                    "name": "Statistical measure",
                    "value": "Calculated result",
                    "interpretation": "Clear explanation of meaning"
                }
            ]
        },
        {
            "heading": "Conclusions and Recommendations",
            "content": "Overall conclusions from analysis",
            "key_conclusions": [
                {
                    "finding": "Key insight",
                    "impact": "Business/analytical impact",
                    "recommendation": "Actionable suggestion"
                }
            ],
            "limitations": [
                "Analysis limitations or caveats"
            ],
            "next_steps": [
                "Recommended actions"
            ]
        }
    ]
}"""
    
    def _optimize_image(self, image_data: bytes) -> bytes:
        """Optimize image for processing"""
        try:
            image = Image.open(io.BytesIO(image_data))
            
            if image.mode == 'RGBA':
                background = Image.new('RGB', image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[3])
                image = background
            
            width, height = image.size
            if width > self.max_image_size[0] or height > self.max_image_size[1]:
                image.thumbnail(self.max_image_size, Image.Resampling.LANCZOS)
            
            quality = self.image_quality
            while quality >= 40:
                output_buffer = io.BytesIO()
                image.save(output_buffer, 
                         format='JPEG', 
                         quality=quality, 
                         optimize=True)
                size_kb = len(output_buffer.getvalue()) / 1024
                
                if size_kb <= self.max_image_size_kb:
                    break
                    
                quality -= 5
            
            optimized_data = output_buffer.getvalue()
            logger.info(f"Image optimized from {len(image_data)/1024:.1f}KB to {len(optimized_data)/1024:.1f}KB")
            
            return optimized_data
        except Exception as e:
            logger.error(f"Image optimization failed: {str(e)}")
            return image_data

    def _clean_json_string(self, text: str) -> Optional[str]:
        """Clean and validate JSON string"""
        try:
            json_match = re.search(r'\{[\s\S]*\}', text)
            if json_match:
                json_str = json_match.group(0)
                json_str = json_str.replace('\n', ' ')
                json_str = re.sub(r'(\w)"(\w)', r'\1\"\2', json_str)
                json_str = re.sub(r',\s*}', '}', json_str)
                return json_str
            return None
        except Exception as e:
            logger.error(f"Failed to clean JSON string: {str(e)}")
            raise DataProcessingError(str(e))

    def _rate_limit_api_call(self):
        """Implement rate limiting for API calls"""
        current_time = time.time()
        time_since_last_call = current_time - self.last_api_call
        
        if time_since_last_call < self.min_delay:
            sleep_time = self.min_delay - time_since_last_call
            time.sleep(sleep_time)
        
        self.last_api_call = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((Exception,))
    )
    def _make_api_call(self, message: HumanMessage) -> str:
        """Make API call with retry logic"""
        self._rate_limit_api_call()
        try:
            return self.llm.invoke([message])
        except Exception as e:
            logger.error(f"API call failed: {str(e)}")
            if "deadline" in str(e).lower():
                self.min_delay += 1
            raise

    def _load_stats_data(self, stats_path: Path) -> Dict:
        """Load and validate stats data from file"""
        try:
            with open(stats_path, 'r') as f:
                stats_data = json.load(f)
            
            # Validate stats data structure
            if not isinstance(stats_data, dict):
                raise DataProcessingError("Invalid stats data format")
            
            return stats_data
        except Exception as e:
            logger.error(f"Failed to load stats data from {stats_path}: {str(e)}")
            raise DataProcessingError(f"Failed to load stats data: {str(e)}")

    @log_execution
    def _process_single_graph(self, graph_path: Path, stats_path: Path) -> Dict:
        """Process a single graph with error handling"""
        try:
            # Read and optimize image
            with open(graph_path, "rb") as img_file:
                image_data = img_file.read()
            optimized_image_data = self._optimize_image(image_data)
            
            # Read and validate stats
            stats_data = self._load_stats_data(stats_path)
            
            # Create comprehensive prompt
            prompt = f"""Statistical Data:
    {json.dumps(stats_data, indent=2)}

    {self.analysis_template}"""

            # Generate analysis
            message = HumanMessage(content=[
                {
                    "type": "text",
                    "text": prompt
                },
                {
                    "type": "image",
                    "image_data": optimized_image_data
                }
            ])

            # Make API call and process response
            response = self._make_api_call(message)
            cleaned_json = self._clean_json_string(response)
            
            if not cleaned_json:
                raise DataProcessingError("Failed to generate valid analysis")
                
            output_data = {
                "graph_name": graph_path.name,
                "question": stats_data.get('question', 'Analyze the visualization'),
                "stats_file": stats_path.name,
                "sections": json.loads(cleaned_json).get("sections", [])
            }
            
            # Save analysis - Remove _analysis suffix
            json_path = path_config.DESCRIPTION_DIR / f"{graph_path.stem}.json"
            with open(json_path, "w", encoding='utf-8') as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"Generated description for {graph_path}")
            return {
                "graph_path": str(graph_path),
                "stats_path": str(stats_path),
                "json_path": str(json_path),
                "content": output_data
            }
                
        except Exception as e:
            logger.error(f"Failed to process graph {graph_path}: {str(e)}")
            return {"error": str(e), "graph_path": str(graph_path)}

    @log_execution
    def generate_description(self, graph_paths: List[str]) -> List[Dict]:
        """Generate descriptions for multiple graphs"""
        try:
            results = []
            # Process graphs in batches
            for i in range(0, len(graph_paths), self.batch_size):
                batch = graph_paths[i:i + self.batch_size]
                batch_results = []
                
                for graph_path in batch:
                    graph_path = Path(graph_path)
                    graph_base = graph_path.stem
                    
                    # Find matching stats file
                    analysis_prefix = '_'.join(graph_base.split('_')[:-1])
                    matching_stats = list(path_config.STATS_DIR.glob(
                        f"{analysis_prefix}*_stats.json"
                    ))
                    
                    if not matching_stats:
                        logger.error(f"No matching stats file found for graph {graph_path.name}")
                        continue
                    
                    # Sort by creation time and take most recent
                    stats_path = sorted(
                        matching_stats,
                        key=lambda x: x.stat().st_ctime
                    )[-1]
                    
                    # Process the graph
                    result = self._process_single_graph(graph_path, stats_path)
                    batch_results.append(result)
                    time.sleep(self.min_delay)
                
                results.extend(batch_results)
                
                # Add delay between batches
                if i + self.batch_size < len(graph_paths):
                    logger.info("Adding delay between batches")
                    time.sleep(self.min_delay * 2)
            
            # Filter out errors
            successful_results = [r for r in results if 'error' not in r]
            errors = [r for r in results if 'error' in r]
            
            # Log any errors
            for error in errors:
                logger.error(f"Failed to process {error['graph_path']}: {error['error']}")
            
            logger.info(f"Successfully processed {len(successful_results)} out of {len(graph_paths)} graphs")
            return successful_results
                
        except Exception as e:
            logger.error(f"Failed to generate descriptions: {str(e)}")
            raise DataProcessingError(str(e))

def generate_descriptions() -> List[Dict]:
    """Main function to generate descriptions"""
    try:
        logger.info("Starting graph analysis...")
        generator = DescriptionGenerator(batch_size=1, min_delay=3.0)
        
        # Get all graph paths
        graph_paths = list(path_config.GRAPHS_DIR.glob('*.png'))
        
        if not graph_paths:
            logger.error("No graphs found for analysis")
            return []
        
        results = generator.generate_description([str(p) for p in graph_paths])
        logger.info(f"Completed processing {len(results)} graphs successfully")
        return results
        
    except Exception as e:
        logger.error(f"Failed to generate descriptions: {str(e)}")
        raise DataProcessingError(str(e))