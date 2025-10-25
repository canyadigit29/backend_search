import asyncio
import json
import os
from app.api.assistant.document_index import generate_document_index

async def generate_real_index():
    """Generate the actual document index from your database"""
    
    print("🚀 Generating document index from real database...")
    print("This may take a moment as it analyzes your document corpus...")
    print("=" * 70)
    
    try:
        # Call the actual index generation endpoint
        result = await generate_document_index()
        
        # The result is a Response object, so we need to get the content
        if hasattr(result, 'body'):
            content = result.body.decode('utf-8')
        else:
            content = str(result)
            
        print("✅ Index generated successfully!")
        print(f"📄 Content length: {len(content)} characters")
        
        # Try to parse as JSON to see structure
        try:
            index_data = json.loads(content)
            print("\n📊 Index Structure:")
            print(f"  - Generated at: {index_data.get('generated_at', 'N/A')}")
            
            corpus_summary = index_data.get('corpus_summary', {})
            print(f"  - Total files: {corpus_summary.get('total_files', 'N/A')}")
            print(f"  - Total chunks: {corpus_summary.get('total_chunks', 'N/A')}")
            print(f"  - Document types: {corpus_summary.get('document_types', {})}")
            
            content_analysis = index_data.get('content_analysis', {})
            print(f"  - Key terms found: {content_analysis.get('key_terms_found', 'N/A')}")
            print(f"  - Ordinance references: {content_analysis.get('ordinance_references', 'N/A')}")
            print(f"  - Common participants: {list(content_analysis.get('common_participants', {}).keys())[:5]}")
            
            # Show sample effective terms
            search_guide = index_data.get('search_strategy_guide', {})
            effective_terms = search_guide.get('effective_terms', {})
            print(f"  - Effective search terms: {list(effective_terms.keys())[:5]}")
            
            print("\n💾 Saving index to file...")
            with open('document_index_sample.json', 'w', encoding='utf-8') as f:
                json.dump(index_data, f, indent=2, ensure_ascii=False)
            print("✅ Saved to: document_index_sample.json")
            
        except json.JSONDecodeError:
            print("⚠️ Could not parse response as JSON. Raw response:")
            print(content[:500] + "..." if len(content) > 500 else content)
        
    except Exception as e:
        print(f"❌ Error generating index: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(generate_real_index())