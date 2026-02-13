import subprocess, os, time, requests, json, signal, uuid

# Use absolute paths for reliability
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARIA2_PATH = os.path.join(BASE_DIR, "bin", "aria2c.exe")
ARIA2_CONF = os.path.join(BASE_DIR, "bin", "aria2.conf")
ARIA2_RPC = "http://127.0.0.1:6888/jsonrpc"

aria2_proc = None
rpc_id = 0
rpc_secret = None  # Can be set from app config

def start_aria2(secret=None):
    global aria2_proc, rpc_secret

    # Use a simpler secret for now to rule out any character encoding issues
    rpc_secret = secret or "gamehub_secure_token_24"

    if not os.path.exists(ARIA2_PATH):
        print(f"[Aria2] Binary missing at {ARIA2_PATH}")
        return

    if aria2_proc and aria2_proc.poll() is None:
        return

    # Force kill any existing aria2 processes to avoid port conflicts
    # We use a broader kill here to ensure nothing is lingering
    try:
        subprocess.run(["taskkill", "/F", "/IM", "aria2c.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2.0) # Increased wait for port release
    except:
        pass

    # Use separate list items to ensure proper quoting in Popen on Windows
    cmd = [
        ARIA2_PATH,
        "--conf-path", ARIA2_CONF,
        "--rpc-secret", rpc_secret,
        "--rpc-listen-port", "6888" # Explicitly force the port
    ]

    print(f"[Aria2] Starting engine on port 6888...")

    try:
        aria2_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW
        )
    except Exception as e:
        print(f"[Aria2] Critical Launch Failure: {e}")
        raise e

    # wait until RPC ready
    for i in range(20):
        if aria2_proc.poll() is not None:
            print(f"[Aria2] Process died with code {aria2_proc.returncode}")
            raise RuntimeError(f"aria2 failed to start (Process exited)")

        try:
            payload = {
                "jsonrpc": "2.0",
                "id": f"health_{i}",
                "method": "aria2.getVersion",
                "params": [f"token:{rpc_secret}"]
            }
            r = requests.post(ARIA2_RPC, json=payload, timeout=2.0)

            if r.status_code == 200:
                resp = r.json()
                if "result" in resp:
                    print(f"[Aria2] Server verified on port 6888 (v{resp['result']['version']})")
                    return
                elif "error" in resp:
                    print(f"[Aria2] Logic Error {resp['error'].get('code')}: {resp['error'].get('message')}")
            else:
                # If we get 401/400, it means a process IS listening but secret is wrong
                print(f"[Aria2] Port 6888 responded with {r.status_code}. Secret check failed.")

        except Exception as e:
            if i % 10 == 0:
                print(f"[Aria2] Waiting for engine (attempt {i})...")
            time.sleep(1.0) # Slower retry for stability

    raise RuntimeError("aria2 RPC failed to authenticate on port 6888.")

def rpc_call(method, params=None):
    global rpc_id, rpc_secret
    rpc_id += 1

    actual_params = params or []
    if rpc_secret:
        # Secret token must be the first parameter
        actual_params = [f"token:{rpc_secret}"] + actual_params

    payload = {
        "jsonrpc":"2.0",
        "id": rpc_id,
        "method": method,
        "params": actual_params
    }

    if not aria2_proc or aria2_proc.poll() is not None:
        raise RuntimeError("Aria2 process not active")

    try:
        r = requests.post(ARIA2_RPC, json=payload, timeout=5)

        if r.status_code != 200:
            print(f"[Aria2 RPC] Error {r.status_code}: {r.text}")
            if r.status_code == 401:
                raise RuntimeError("aria2 RPC Unauthorized - check secret")

        r.raise_for_status()
        resp = r.json()
        if "error" in resp:
            err_msg = resp['error'].get('message', 'Unknown Error')
            print(f"[Aria2 RPC] Logical Error: {err_msg}")
            raise RuntimeError(f"aria2 ERROR: {err_msg}")
        return resp["result"]
    except Exception as e:
        if not isinstance(e, RuntimeError):
            print(f"[Aria2 RPC] Network/HTTP Error: {e}")
        raise e
