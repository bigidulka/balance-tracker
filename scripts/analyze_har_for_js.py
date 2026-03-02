import json

def main():
    try:
        with open('tests/debank_analysis_463452c3.json', 'r') as f:
            data = json.load(f)
            
        print("API Patterns:")
        print(json.dumps(data.get('api_patterns', {}), indent=2))
        
        print("\nJS State:")
        print(json.dumps(data.get('javascript_state', {}), indent=2))
        
    except Exception as e:
        print(f"Error reading file: {e}")

if __name__ == "__main__":
    main()
