"""
Document Index Generator for Custom GPT Assistant

This module generates a comprehensive index of the document corpus that can be 
downloaded and uploaded to a Custom GPT to improve search strategy and user guidance.
"""

import json
import os
from collections import defaultdict, Counter
from datetime import datetime
from typing import Dict, List, Any, Tuple
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response
import httpx

from app.core.supabase_client import create_client
from app.core.openai_client import chat_completion

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_ROLE = os.environ["SUPABASE_SERVICE_ROLE"]
supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)

router = APIRouter()


def extract_key_terms_from_content(content: str, max_terms: int = 10) -> List[str]:
    """
    Extract key terms from document content using simple heuristics.
    Returns important-looking terms that might be useful for search.
    """
    if not content:
        return []
    
    # Clean and split content
    content = re.sub(r'[^\w\s\-\.]', ' ', content)
    words = content.split()
    
    # Find potential key terms (capitalized words, numbers, specific patterns)
    key_terms = []
    for word in words:
        word = word.strip('.,;:()"\'')
        if not word:
            continue
            
        # Add capitalized words (likely proper nouns) - but filter common words
        if (word[0].isupper() and len(word) > 2 and 
            word.upper() not in ['THE', 'AND', 'FOR', 'WITH', 'PAGE', 'THAT', 'THIS', 'BOROUGH']):
            key_terms.append(word)
        
        # Add numbers that might be ordinance numbers, years, etc.
        if re.match(r'^\d{2,4}$', word):
            key_terms.append(word)
        
        # Add hyphenated terms (often important identifiers)
        if '-' in word and len(word) > 3:
            key_terms.append(word)
            
        # Look for ordinance patterns
        if re.match(r'^\d{1,4}[-/]\d{4}$', word):  # Like "123-2023"
            key_terms.append(word)
    
    # Count frequency and return most common
    term_counts = Counter(key_terms)
    return [term for term, count in term_counts.most_common(max_terms)]


def extract_metadata_from_content(content: str) -> Dict[str, Any]:
    """
    Extract structured metadata from content text since the metadata column is empty.
    Looks for patterns like dates, ordinance numbers, motions, etc.
    """
    metadata = {}
    
    if not content:
        return metadata
    
    # Look for ordinance numbers in various formats
    ordinance_patterns = [
        r'[Oo]rdinance\s+(?:No\.?\s*)?(\d{1,4}[-/]?\d{2,4})',
        r'[Oo]rdinance\s+(\d{1,4})',
        r'Resolution\s+(?:No\.?\s*)?(\d{1,4}[-/]?\d{2,4})'
    ]
    
    for pattern in ordinance_patterns:
        matches = re.findall(pattern, content)
        if matches:
            metadata['ordinance_references'] = matches
            break
    
    # Look for motion patterns
    motion_patterns = [
        r'[Mm]oved by ([^,\n]+)',
        r'[Mm]otion by ([^,\n]+)',
        r'[Ss]econd(?:ed)? by ([^,\n\.]+)'
    ]
    
    motions = []
    for pattern in motion_patterns:
        matches = re.findall(pattern, content)
        # Clean up the matches
        cleaned_matches = [match.strip() for match in matches if len(match.strip()) < 50]  # Avoid long extractions
        motions.extend(cleaned_matches)
    
    if motions:
        metadata['motion_participants'] = list(set(motions))  # Remove duplicates
    
    # Look for dollar amounts (budget/financial info)
    money_pattern = r'\$[\d,]+(?:\.\d{2})?'
    amounts = re.findall(money_pattern, content)
    if amounts:
        metadata['financial_amounts'] = amounts[:5]  # Limit to first 5
    
    # Look for addresses/locations
    address_pattern = r'\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Way|Boulevard|Blvd)'
    addresses = re.findall(address_pattern, content)
    if addresses:
        metadata['addresses'] = addresses[:3]  # Limit to first 3
    
    # Look for dates mentioned in content
    date_patterns = [
        r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}',
        r'\d{1,2}/\d{1,2}/\d{2,4}'
    ]
    
    dates_found = []
    for pattern in date_patterns:
        matches = re.findall(pattern, content)
        dates_found.extend(matches)
    
    if dates_found:
        metadata['content_dates'] = dates_found[:3]  # Limit to first 3
    
    return metadata


def analyze_search_effectiveness(query_terms: List[str]) -> Dict[str, Any]:
    """
    Test how effective different search terms would be by doing sample searches.
    This helps the GPT understand which terms yield good results.
    """
    effectiveness = {}
    
    for term in query_terms[:5]:  # Limit to avoid too many API calls
        try:
            # Do a simple FTS search to see how many results this term gets
            rpc_args = {
                "keyword_query": term,
                "match_count": 50
            }
            
            supabase_url = os.environ["SUPABASE_URL"].rstrip("/")
            service_role = os.environ["SUPABASE_SERVICE_ROLE"]
            headers = {
                "apikey": service_role,
                "Authorization": f"Bearer {service_role}",
                "Content-Type": "application/json"
            }
            
            endpoint = f"{supabase_url}/rest/v1/rpc/match_documents_fts_v3"
            response = httpx.post(endpoint, headers=headers, json=rpc_args, timeout=10)
            
            if response.status_code == 200:
                results = response.json() or []
                effectiveness[term] = {
                    "result_count": len(results),
                    "avg_score": sum(r.get("ts_rank", 0) for r in results) / len(results) if results else 0,
                    "top_files": list(set([r.get("file_name") for r in results[:5] if r.get("file_name")]))
                }
            else:
                effectiveness[term] = {"result_count": 0, "avg_score": 0, "top_files": []}
                
        except Exception as e:
            print(f"[DEBUG] Error testing term '{term}': {e}")
            effectiveness[term] = {"result_count": 0, "avg_score": 0, "top_files": []}
    
    return effectiveness


def generate_topic_taxonomy(document_stats: Dict) -> Dict[str, Any]:
    """
    Generate a hierarchical topic structure based on document types and content analysis.
    """
    taxonomy = {
        "governance": {
            "description": "Municipal governance, meetings, decisions",
            "document_types": ["meeting_minutes", "transcripts", "agendas"],
            "key_terms": ["council", "mayor", "vote", "motion", "resolution"],
            "search_tips": "Use meeting dates, specific council member names, or motion topics"
        },
        "ordinances": {
            "description": "Local laws, regulations, and legal documents",
            "document_types": ["ordinance", "code", "regulation"],
            "key_terms": ["title", "section", "ordinance", "code", "violation"],
            "search_tips": "Search by ordinance number, title reference, or specific code section"
        },
        "finance": {
            "description": "Budget, funding, financial matters",
            "document_types": ["budget", "financial_report", "audit"],
            "key_terms": ["budget", "fund", "revenue", "expense", "CDBG", "ARPA"],
            "search_tips": "Use specific fund names, dollar amounts, or fiscal years"
        },
        "infrastructure": {
            "description": "Public works, utilities, maintenance",
            "document_types": ["engineering_report", "project_plan", "inspection"],
            "key_terms": ["sewer", "water", "road", "bridge", "maintenance", "ADA"],
            "search_tips": "Include street names, project types, or infrastructure categories"
        },
        "development": {
            "description": "Planning, zoning, building permits",
            "document_types": ["permit", "zoning", "planning"],
            "key_terms": ["zoning", "permit", "development", "planning", "variance"],
            "search_tips": "Use property addresses, applicant names, or zoning classifications"
        }
    }
    
    return taxonomy


@router.get("/assistant/generate_document_index")
async def generate_document_index():
    """
    Generate a comprehensive index of the document corpus for use by Custom GPT.
    Returns a downloadable JSON file containing metadata to improve search strategies.
    """
    try:
        print("[DEBUG] Starting document index generation...")
        
        # 1. Get basic document statistics
        files_result = supabase.table("files").select("id,file_name,file_type,created_at").execute()
        files_data = getattr(files_result, "data", [])
        
        chunks_result = supabase.table("document_chunks").select(
            "id,file_id,document_type,meeting_date,ordinance_title,ordinance_number,content,page_number,chunk_index"
        ).limit(2000).execute()  # Increased limit since we need more content to analyze
        chunks_data = getattr(chunks_result, "data", [])
        
        print(f"[DEBUG] Analyzing {len(files_data)} files and {len(chunks_data)} chunks...")
        
        # 2. Analyze document types and patterns
        doc_type_stats = Counter()
        meeting_dates = []
        ordinance_info = []
        file_type_stats = Counter()
        content_sample = []
        extracted_metadata = []  # Store metadata extracted from content
        
        file_lookup = {f["id"]: f for f in files_data}
        
        for chunk in chunks_data:
            # Document type analysis
            doc_type = chunk.get("document_type") or "unknown"
            doc_type_stats[doc_type] += 1
            
            # Meeting date analysis
            if chunk.get("meeting_date"):
                meeting_dates.append(chunk["meeting_date"])
            
            # Ordinance analysis (from both DB fields and content)
            if chunk.get("ordinance_title") or chunk.get("ordinance_number"):
                ordinance_info.append({
                    "title": chunk.get("ordinance_title"),
                    "number": chunk.get("ordinance_number"),
                    "file_id": chunk.get("file_id"),
                    "source": "database"
                })
            
            # Extract metadata from content since DB metadata column is empty
            content = chunk.get("content", "")
            if content:
                content_metadata = extract_metadata_from_content(content)
                if content_metadata:
                    extracted_metadata.append({
                        "chunk_id": chunk.get("id"),
                        "file_id": chunk.get("file_id"),
                        "metadata": content_metadata
                    })
                
                # Add ordinances found in content
                if 'ordinance_references' in content_metadata:
                    for ord_ref in content_metadata['ordinance_references']:
                        ordinance_info.append({
                            "title": None,
                            "number": ord_ref,
                            "file_id": chunk.get("file_id"),
                            "source": "content_extracted"
                        })
            
            # Content sampling for key term extraction
            if content and len(content_sample) < 100:  # Increased sample size
                content_sample.append(content[:1000])  # Larger samples
        
        for file_data in files_data:
            file_type = file_data.get("file_type") or "unknown"
            file_type_stats[file_type] += 1
        
        # 3. Extract key terms from content sample
        all_content = " ".join(content_sample)
        key_terms = extract_key_terms_from_content(all_content, max_terms=100)  # More terms
        
        # 4. Analyze content-extracted metadata patterns
        content_patterns = {
            "common_participants": Counter(),
            "financial_references": [],
            "address_mentions": [],
            "ordinance_numbers": []
        }
        
        for meta_item in extracted_metadata:
            metadata = meta_item["metadata"]
            
            # Collect motion participants
            if "motion_participants" in metadata:
                for participant in metadata["motion_participants"]:
                    content_patterns["common_participants"][participant.strip()] += 1
            
            # Collect financial amounts
            if "financial_amounts" in metadata:
                content_patterns["financial_references"].extend(metadata["financial_amounts"])
            
            # Collect addresses  
            if "addresses" in metadata:
                content_patterns["address_mentions"].extend(metadata["addresses"])
                
            # Collect ordinance references
            if "ordinance_references" in metadata:
                content_patterns["ordinance_numbers"].extend(metadata["ordinance_references"])
        
        # 5. Analyze search effectiveness for top terms
        search_effectiveness = analyze_search_effectiveness(key_terms[:15])  # Test more terms
        
        # 5. Generate date ranges
        date_ranges = {}
        if meeting_dates:
            sorted_dates = sorted(meeting_dates)
            date_ranges = {
                "earliest_meeting": sorted_dates[0],
                "latest_meeting": sorted_dates[-1],
                "total_meetings": len(set(meeting_dates)),
                "years_covered": list(set([d[:4] for d in sorted_dates if d]))
            }
        
        # 6. Build the comprehensive index
        document_index = {
            "generated_at": datetime.now().isoformat(),
            "corpus_summary": {
                "total_files": len(files_data),
                "total_chunks": len(chunks_data),
                "document_types": dict(doc_type_stats),
                "file_types": dict(file_type_stats),
                "date_coverage": date_ranges,
                "metadata_status": "extracted_from_content"  # Note that metadata came from content parsing
            },
            "content_analysis": {
                "key_terms_found": len(key_terms),
                "common_participants": dict(content_patterns["common_participants"].most_common(10)),
                "financial_references_count": len(content_patterns["financial_references"]),
                "addresses_found": len(set(content_patterns["address_mentions"])),
                "ordinance_references": len(set(content_patterns["ordinance_numbers"])),
                "sample_ordinances": list(set(content_patterns["ordinance_numbers"]))[:10]
            },
            "search_strategy_guide": {
                "effective_terms": {
                    term: {
                        "result_count": data["result_count"],
                        "confidence": "high" if data["result_count"] > 10 else "medium" if data["result_count"] > 3 else "low",
                        "sample_files": data["top_files"][:3]
                    }
                    for term, data in search_effectiveness.items()
                },
                "recommended_weights": {
                    "semantic_heavy_queries": {
                        "description": "Conceptual questions, explanations, broad topics",
                        "weights": {"semantic": 0.75, "keyword": 0.25},
                        "threshold": 0.4,
                        "examples": ["explain budget process", "describe zoning policies"]
                    },
                    "keyword_heavy_queries": {
                        "description": "Specific entities, numbers, exact phrases",
                        "weights": {"semantic": 0.3, "keyword": 0.7},
                        "threshold": 0.6,
                        "examples": ["Ordinance 1045", "CDBG funding", "ADA compliance"]
                    },
                    "balanced_queries": {
                        "description": "Mixed entity + concept queries",
                        "weights": {"semantic": 0.5, "keyword": 0.5},
                        "threshold": 0.45,
                        "examples": ["storm sewer maintenance on Main Street"]
                    }
                }
            },
            "topic_taxonomy": generate_topic_taxonomy(doc_type_stats),
            "document_catalog": {
                "by_type": {
                    doc_type: {
                        "count": count,
                        "description": f"Documents of type: {doc_type}",
                        "sample_search_terms": key_terms[:5] if doc_type in ["meeting_minutes", "ordinance"] else []
                    }
                    for doc_type, count in doc_type_stats.most_common(10)
                },
                "ordinances": [
                    {
                        "title": ord_info.get("title"),
                        "number": ord_info.get("number"),
                        "source": ord_info.get("source", "unknown"),
                        "search_hint": f"Use exact number '{ord_info.get('number')}' for best results" if ord_info.get("number") else "Use title keywords"
                    }
                    for ord_info in ordinance_info[:30] if ord_info.get("title") or ord_info.get("number")
                ],
                "key_entities": key_terms[:50],  # More entities
                "common_people": list(content_patterns["common_participants"].keys())[:15],
                "sample_addresses": list(set(content_patterns["address_mentions"]))[:10]
            },
            "search_optimization_tips": {
                "high_precision_needed": [
                    "Use quoted phrases for exact matches",
                    "Include document type filters when known",
                    "Specify date ranges for time-sensitive queries",
                    "Use ordinance numbers or titles for legal documents"
                ],
                "broad_exploration": [
                    "Use semantic-heavy weights for conceptual queries", 
                    "Try multiple related terms with OR logic",
                    "Lower relevance threshold for comprehensive coverage",
                    "Use topic-based terms from the taxonomy"
                ],
                "when_no_results": [
                    "Try alternative terminology or synonyms",
                    "Broaden the query to related concepts",
                    "Check if the topic might be in a different document type",
                    "Consider if the information might be too recent/old for the corpus"
                ]
            },
            "usage_instructions": {
                "before_each_search": [
                    "Check if the topic exists in the document catalog",
                    "Choose appropriate search weights based on query type",
                    "Consider using effective terms from the search guide",
                    "Set relevance threshold based on precision needs"
                ],
                "query_enhancement": [
                    "Add related terms from topic taxonomy",
                    "Include entity names when relevant",
                    "Use OR logic for term variations",
                    "Consider document type context"
                ]
            }
        }
        
        print(f"[DEBUG] Generated index with {len(key_terms)} key terms and {len(ordinance_info)} ordinances")
        
        # Return as downloadable JSON
        json_content = json.dumps(document_index, indent=2, ensure_ascii=False)
        
        return Response(
            content=json_content,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=document_index_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            }
        )
        
    except Exception as e:
        print(f"[ERROR] Failed to generate document index: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate document index: {str(e)}")


@router.get("/assistant/index_stats")
async def get_index_stats():
    """
    Get quick statistics about the document corpus without generating full index.
    Useful for checking if regeneration is needed.
    """
    try:
        files_count = supabase.table("files").select("id", count="exact").execute()
        chunks_count = supabase.table("document_chunks").select("id", count="exact").execute()
        
        doc_types = supabase.table("document_chunks").select("document_type").execute()
        doc_type_counts = Counter([d.get("document_type", "unknown") for d in doc_types.data])
        
        return JSONResponse({
            "total_files": files_count.count,
            "total_chunks": chunks_count.count,
            "document_types": dict(doc_type_counts),
            "last_checked": datetime.now().isoformat()
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get index stats: {str(e)}")