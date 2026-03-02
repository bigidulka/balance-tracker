import json

def analyze_har():
    try:
        with open('tests/debank_analysis_463452c3.json', 'r') as f:
            data = json.load(f)
            
        print("Finding the secret used for 'random_id'...")
        
        for p in data.get('api_patterns', []):
            headers = p.get('headers', {})
            account_hdr = headers.get('account')
            if account_hdr:
                try:
                    account_data = json.loads(account_hdr)
                    random_id = account_data.get('random_id')
                    print(f"random_id found: {random_id}")
                except Exception:
                    pass
            
            x_api_sign = headers.get('x-api-sign')
            if x_api_sign:
                print(f"x-api-sign found: {x_api_sign}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    analyze_har()
