import requests
import concurrent.futures
import time

# 1. Working US-only proxy list
PROXY_SOURCES = [
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/countries/US/data.txt"
]

def fetch_proxies():
    proxies = set()
    print("Fetching raw proxy lists...")
    for url in PROXY_SOURCES:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            for line in response.text.splitlines():
                proxy = line.strip()
                if proxy and ":" in proxy:
                    if not proxy.startswith(('http://', 'https://', 'socks4://', 'socks5://')):
                        proxy = f"http://{proxy}"
                    proxies.add(proxy)
        except Exception as e:
            print(f"Failed to fetch {url}: {e}")
    return list(proxies)

def validate_and_format(proxy):
    try:
        # We use ip-api.com to double-check it's actually US
        test_url = "http://ip-api.com/json"
        proxies_dict = {"http": proxy, "https": proxy}
        
        start_time = time.time()
        # Use a slightly longer timeout for slow proxies
        response = requests.get(test_url, proxies=proxies_dict, timeout=6) 
        elapsed_time = time.time() - start_time
        
        if response.status_code == 200:
            data = response.json()
            if data.get("countryCode") == "US":
                if elapsed_time < 2.0:
                    speed = "fast"
                elif elapsed_time < 4.0:
                    speed = "medium"
                else:
                    speed = "slow"
                
                # Small sleep to prevent getting rate-limited by the API
                time.sleep(0.5) 
                return f"{proxy} check US {speed}"
    except Exception:
        pass
    return None

if __name__ == "__main__":
    all_proxies = fetch_proxies()
    print(f"Total raw US proxies fetched: {len(all_proxies)}")
    print("Validating (lowering speed to avoid API rate limits)...")

    working_proxies = []
    
    # REDUCED to 10 workers. If we use 50, ip-api.com will block us for making too many requests too fast.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(validate_and_format, all_proxies)
        
        for result in results:
            if result:
                working_proxies.append(result)
                print(f"✅ {result}")

    if working_proxies:
        with open("us_proxies.txt", "w") as f:
            for proxy in working_proxies:
                f.write(f"{proxy}\n")
        print(f"\nSuccess! Saved {len(working_proxies)} working US proxies to us_proxies.txt")
    else:
        print("\nNo working US proxies found. Try again later.")

