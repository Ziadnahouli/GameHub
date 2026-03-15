import time
import requests
import logging
from datetime import datetime, timezone

# We assume movie_manager and providers are in the same directory
from movie_manager import movie_manager, _extract_episode_number

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("CloudTracker")

# ======================================================================
# CONFIGURE THIS URL TO MATCH YOUR FIREBASE
# Example: https://my-gamehub-project.firebaseio.com/
# Make sure it ends with a slash!
# ======================================================================
FIREBASE_URL = "https://gamehub-c726d-default-rtdb.europe-west1.firebasedatabase.app/"

def check_firebase_url():
    if not FIREBASE_URL or not FIREBASE_URL.startswith("http"):
        logger.error("FIREBASE_URL is not set. Please edit cloud_tracker.py and set FIREBASE_URL to your Firebase Database URL.")
        exit(1)

def pull_all_users():
    endpoint = f"{FIREBASE_URL}users.json"
    try:
        resp = requests.get(endpoint, timeout=10)
        if resp.status_code == 200:
            return resp.json() or {}
        else:
            logger.error(f"Failed to fetch users from Firebase: {resp.status_code}")
    except Exception as e:
        logger.error(f"Error fetching users from Firebase: {e}")
    return {}

def update_user_data(ntfy_topic, tracked_dict):
    endpoint = f"{FIREBASE_URL}users/{ntfy_topic}.json"
    payload = {
        "tracked": tracked_dict,
        "ntfy_topic": ntfy_topic
    }
    try:
        requests.put(endpoint, json=payload, timeout=10)
        logger.info(f"Updated Firebase for user: {ntfy_topic}")
    except Exception as e:
        logger.error(f"Error updating Firebase for {ntfy_topic}: {e}")

def notify_phone(ntfy_topic, title, message):
    if not ntfy_topic:
        return
    logger.info(f"Sending notification to {ntfy_topic}: {title}")
    try:
        requests.post(
            f"https://ntfy.sh/{ntfy_topic}",
            data=message.encode('utf-8'),
            headers={
                "Title": title.encode('utf-8'),
                "Tags": "tv,popcorn"
            },
            timeout=5
        )
    except Exception as e:
        logger.error(f"Failed to send ntfy for {ntfy_topic}: {e}")

def main_loop():
    check_firebase_url()
    
    logger.info("Multi-User Cloud Tracker starting. Press Ctrl+C to stop.")
    while True:
        try:
            logger.info("Polling Firebase for all users...")
            users_data = pull_all_users()
            
            if not users_data:
                logger.info("No users found in database. Sleeping manual.")
                time.sleep(3600)
                continue
                
            for user_topic, data in users_data.items():
                tracked = data.get("tracked", {})
                if not tracked:
                    continue
                
                logger.info(f"Processing updates for user topic: {user_topic}")
                changes_made = False
                
                for content_id, info in tracked.items():
                    try:
                        # Skip if checked recently (within 55 mins) to avoid redundant requests
                        last_checked_str = info.get("last_checked")
                        if last_checked_str:
                            last_checked = datetime.fromisoformat(last_checked_str)
                            if (datetime.now(timezone.utc) - last_checked).total_seconds() < 3300:
                                continue

                        logger.info(f"  > Checking {info.get('title', content_id)}")
                        details = movie_manager.get_details(content_id)
                        if not details or not details.get("links"):
                            continue

                        eps = []
                        for link in details["links"]:
                            ep_val = _extract_episode_number(link)
                            if ep_val and ep_val.isdigit():
                                eps.append(int(ep_val))
                        
                        if not eps:
                            continue
                        
                        current_latest = max(eps)
                        last_notified = info.get("last_notified_episode", info.get("last_episode", 0))

                        if current_latest > last_notified:
                            title = info.get("title", "New Episode!")
                            msg = f"Episode {current_latest} is now available!"
                            notify_phone(user_topic, title, msg)

                            tracked[content_id]["last_episode"] = current_latest
                            tracked[content_id]["last_notified_episode"] = current_latest
                        
                        tracked[content_id]["last_checked"] = datetime.now(timezone.utc).isoformat()
                        changes_made = True
                            
                    except Exception as e:
                        logger.error(f"Error checking {content_id} for {user_topic}: {e}")
                        continue
                        
                if changes_made:
                    update_user_data(user_topic, tracked)
                
        except Exception as e:
            logger.error(f"Critical error in main loop: {e}")
            
        logger.info("All users processed. Sleeping for 1 hour...")
        time.sleep(3600)

if __name__ == "__main__":
    main_loop()

if __name__ == "__main__":
    main_loop()
