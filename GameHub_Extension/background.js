/**
 * background.js - Game Hub Pro Service Worker
 * Version: 6.4 (Privacy & Advanced Interception)
 */

const API_URL = "http://127.0.0.1:5000/api/downloads/add";
const REPORT_URL = "http://127.0.0.1:5000/api/extension/report_version";
const FORMATS_API = "http://127.0.0.1:5000/api/video/formats";

// High-value file types to intercept
const PRIORITY_EXTENSIONS = /\.(exe|msi|zip|rar|7z|iso|bin|pkg|apk|dmg|mp4|mkv|mov|avi|flac|wav|mp3)($|\?)/i;

// Storage for active interceptions [Key: downloadId]
const activeInterceptions = new Map();

// ============================================================================
// [1] HEARTBEAT SYSTEM (Desktop App Sync)
// ============================================================================

async function sendHeartbeat() {
    const currentVersion = chrome.runtime.getManifest().version;
    const url = `http://127.0.0.1:5000/api/extension/report_version?nocache=${Date.now()}`;

    try {
        const response = await fetch(url, {
            method: 'POST',
            mode: 'cors',
            cache: 'no-cache',
            keepalive: true,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ version: currentVersion })
        });

        if (!response.ok) throw new Error(`Server responded with ${response.status}`);
        const data = await response.json();

        if (data.command === "RELOAD") {
            console.log("%c[GHP] REMOTE RELOAD SIGNAL DETECTED", "color: #f1c40f; font-weight: bold;");
            setTimeout(() => {
                console.log("[GHP] Restarting service worker now...");
                chrome.runtime.reload();
            }, 500);
        } else {
            console.log(`[Pulse] Connected to Hub: v${currentVersion}`);
        }

    } catch (err) {
        console.debug("[Pulse] Backend relay is currently offline.");
    }
}

chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === 'ghp_heartbeat_alarm') {
        sendHeartbeat();
    }
});

// Send immediate heartbeat on startup and then create periodic alarm
sendHeartbeat();
setTimeout(() => sendHeartbeat(), 2000); // Second heartbeat after 2 seconds

// Dual-Polling Strategy:
// 1. chrome.alarms: Reliable for waking up the service worker if it goes to sleep.
chrome.alarms.create('ghp_heartbeat_alarm', { periodInMinutes: 1 });

// 2. setInterval: Aggressive polling (10s) while the service worker is active.
// This ensures that during an update/reload window, the RELOAD signal is caught instantly.
setInterval(sendHeartbeat, 10000);

// A. onDeterminingFilename: The Gold Standard for Interception
chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
    // 1. Ignore internal/blob/sensitive
    if (item.url.startsWith('blob:') || item.url.startsWith('data:') || item.url.startsWith('chrome:') || item.url.startsWith('chrome-extension:')) {
        console.log(`[Interceptor] IGNORED: ${item.filename || 'Unknown'} - Reason: Blob/Internal URL`);
        return;
    }

    // 2. Analyze Filename & Size
    const isLarge = item.fileSize > 5000000; // 5MB+
    const isArchive = PRIORITY_EXTENSIONS.test(item.filename || item.url);

    // 3. Parse Content-Disposition if filename missing
    let filename = item.filename;
    if (!filename || filename === '') {
        // Try to extract from URL
        const urlPath = new URL(item.url).pathname;
        filename = urlPath.split('/').pop() || 'download';
    }

    // 4. Decision Logic with Reason Codes
    let reason = null;
    if (isLarge && isArchive) {
        reason = "Large Archive";
    } else if (isLarge) {
        reason = "Large File";
    } else if (isArchive) {
        reason = "Archive";
    }

    if (reason) {
        console.log(`[Interceptor] INTERCEPTED: ${filename} - Reason: ${reason}`);
        chrome.downloads.pause(item.id, async () => {
            const success = await sendToGameHub(item.finalUrl || item.url, item.referrer, null, { ...item, filename, reason });
            if (success) {
                chrome.downloads.cancel(item.id);
                chrome.downloads.erase({ id: item.id });

                // Show success notification with error handling
                try {
                    chrome.notifications.create({
                        type: 'basic',
                        iconUrl: chrome.runtime.getURL('icon.png'),
                        title: 'Game Hub Pro',
                        message: `✅ Sent to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                    }, (notificationId) => {
                        if (chrome.runtime.lastError) {
                            console.error('[GHP] Notification error:', chrome.runtime.lastError);
                            // Fallback: try without icon
                            chrome.notifications.create({
                                type: 'basic',
                                title: 'Game Hub Pro',
                                message: `✅ Sent to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                            });
                        } else {
                            console.log('[GHP] onCreated success notification created:', notificationId);
                        }
                    });
                } catch (e) {
                    console.error('[GHP] Failed to create notification:', e);
                }

                // Set badge for 2 seconds
                chrome.action.setBadgeText({ text: '✓' }, () => {
                    if (chrome.runtime.lastError) {
                        console.error('[GHP] Badge error:', chrome.runtime.lastError);
                    }
                });
                chrome.action.setBadgeBackgroundColor({ color: '#2ecc71' });
                setTimeout(() => {
                    chrome.action.setBadgeText({ text: '' });
                }, 2000);
            } else {
                chrome.downloads.resume(item.id);

                // Show failure notification with error handling
                chrome.notifications.getPermissionLevel((level) => {
                    if (level === 'denied') {
                        console.warn('[GHP] Notification permission denied');
                        return;
                    }

                    try {
                        chrome.notifications.create({
                            type: 'basic',
                            iconUrl: chrome.runtime.getURL('icon.png'),
                            title: 'Game Hub Pro',
                            message: `❌ Failed to send — resuming browser download: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                        }, (notificationId) => {
                            if (chrome.runtime.lastError) {
                                console.error('[GHP] Notification error:', chrome.runtime.lastError);
                                // Fallback: try without icon
                                chrome.notifications.create({
                                    type: 'basic',
                                    title: 'Game Hub Pro',
                                    message: `❌ Failed to send — resuming browser download: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                }, (fallbackId) => {
                                    if (chrome.runtime.lastError) {
                                        console.error('[GHP] Fallback notification also failed:', chrome.runtime.lastError);
                                    } else {
                                        console.log('[GHP] Fallback notification created:', fallbackId);
                                    }
                                });
                            } else {
                                console.log('[GHP] Failure notification created:', notificationId);
                            }
                        });
                    } catch (e) {
                        console.error('[GHP] Failed to create notification:', e);
                    }
                });
            }
        });
    } else {
        console.log(`[Interceptor] IGNORED: ${filename} - Reason: Not priority file type`);
    }
});

// B. onCreated: Fallback for fast-starting downloads
chrome.downloads.onCreated.addListener((item) => {
    if (!item || !item.url || item.url.startsWith('blob:') || item.url.startsWith('data:') || item.url.startsWith('chrome:') || item.url.startsWith('chrome-extension:')) {
        return;
    }

    if (PRIORITY_EXTENSIONS.test(item.url)) {
        chrome.downloads.pause(item.id, async () => {
            // We wait briefly to see if onDeterminingFilename handled it
            setTimeout(async () => {
                const exists = await new Promise(r => chrome.downloads.search({ id: item.id }, d => r(d && d[0])));
                if (exists && exists.state === 'in_progress' && exists.paused) {
                    const filename = item.filename || item.url.split('/').pop() || 'download';
                    const success = await sendToGameHub(item.finalUrl || item.url, item.referrer, null, { ...item, reason: "Manual" });
                    if (success) {
                        chrome.downloads.cancel(item.id);

                        // Show success notification with error handling
                        chrome.notifications.getPermissionLevel((level) => {
                            if (level === 'denied') {
                                console.warn('[GHP] Notification permission denied');
                                return;
                            }

                            try {
                                chrome.notifications.create({
                                    type: 'basic',
                                    iconUrl: chrome.runtime.getURL('icon.png'),
                                    title: 'Game Hub Pro',
                                    message: `✅ Sent to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                }, (notificationId) => {
                                    if (chrome.runtime.lastError) {
                                        console.error('[GHP] Notification error:', chrome.runtime.lastError);
                                        // Fallback: try without icon
                                        chrome.notifications.create({
                                            type: 'basic',
                                            title: 'Game Hub Pro',
                                            message: `✅ Sent to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                        }, (fallbackId) => {
                                            if (chrome.runtime.lastError) {
                                                console.error('[GHP] Fallback notification also failed:', chrome.runtime.lastError);
                                            } else {
                                                console.log('[GHP] Fallback notification created:', fallbackId);
                                            }
                                        });
                                    } else {
                                        console.log('[GHP] onCreated success notification created:', notificationId);
                                    }
                                });
                            } catch (e) {
                                console.error('[GHP] Failed to create notification:', e);
                            }
                        });

                        // Set badge for 2 seconds
                        chrome.action.setBadgeText({ text: '✓' }, () => {
                            if (chrome.runtime.lastError) {
                                console.error('[GHP] Badge error:', chrome.runtime.lastError);
                            }
                        });
                        chrome.action.setBadgeBackgroundColor({ color: '#2ecc71' });
                        setTimeout(() => {
                            chrome.action.setBadgeText({ text: '' });
                        }, 2000);
                    } else {
                        chrome.downloads.resume(item.id);

                        // Show failure notification with error handling
                        chrome.notifications.getPermissionLevel((level) => {
                            if (level === 'denied') {
                                console.warn('[GHP] Notification permission denied');
                                return;
                            }

                            try {
                                chrome.notifications.create({
                                    type: 'basic',
                                    iconUrl: chrome.runtime.getURL('icon.png'),
                                    title: 'Game Hub Pro',
                                    message: `❌ Failed to send — resuming browser download: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                }, (notificationId) => {
                                    if (chrome.runtime.lastError) {
                                        console.error('[GHP] Notification error:', chrome.runtime.lastError);
                                        // Fallback: try without icon
                                        chrome.notifications.create({
                                            type: 'basic',
                                            title: 'Game Hub Pro',
                                            message: `❌ Failed to send — resuming browser download: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                        }, (fallbackId) => {
                                            if (chrome.runtime.lastError) {
                                                console.error('[GHP] Fallback notification also failed:', chrome.runtime.lastError);
                                            } else {
                                                console.log('[GHP] Fallback notification created:', fallbackId);
                                            }
                                        });
                                    } else {
                                        console.log('[GHP] onCreated failure notification created:', notificationId);
                                    }
                                });
                            } catch (e) {
                                console.error('[GHP] Failed to create notification:', e);
                            }
                        });
                    }
                }
            }, 100);
        });
    }
});

// ============================================================================
// [3] MESSAGE HUB
// ============================================================================

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {

    if (message.action === "send_to_app") {
        const filename = message.filename || message.url.split('/').pop() || 'Manual Download';
        sendToGameHub(message.url, sender.tab?.url, message.format_id, { filename: filename })
            .then(success => {
                if (success && message.downloadId) {
                    chrome.downloads.cancel(message.downloadId, () => {
                        chrome.downloads.erase({ id: message.downloadId });
                    });
                }

                // Show notification for manual sends with error handling
                if (success) {
                    chrome.notifications.getPermissionLevel((level) => {
                        if (level === 'denied') {
                            console.warn('[GHP] Notification permission denied');
                            return;
                        }

                        try {
                            chrome.notifications.create({
                                type: 'basic',
                                iconUrl: chrome.runtime.getURL('icon.png'),
                                title: 'Game Hub Pro',
                                message: `✅ Sent to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                            }, (notificationId) => {
                                if (chrome.runtime.lastError) {
                                    console.error('[GHP] Notification error:', chrome.runtime.lastError);
                                    // Fallback: try without icon
                                    chrome.notifications.create({
                                        type: 'basic',
                                        title: 'Game Hub Pro',
                                        message: `✅ Sent to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                    }, (fallbackId) => {
                                        if (chrome.runtime.lastError) {
                                            console.error('[GHP] Fallback notification also failed:', chrome.runtime.lastError);
                                        } else {
                                            console.log('[GHP] Fallback notification created:', fallbackId);
                                        }
                                    });
                                } else {
                                    console.log('[GHP] Manual notification created:', notificationId);
                                }
                            });
                        } catch (e) {
                            console.error('[GHP] Failed to create notification:', e);
                        }
                    });

                    chrome.action.setBadgeText({ text: '✓' }, () => {
                        if (chrome.runtime.lastError) {
                            console.error('[GHP] Badge error:', chrome.runtime.lastError);
                        }
                    });
                    chrome.action.setBadgeBackgroundColor({ color: '#2ecc71' });
                    setTimeout(() => {
                        chrome.action.setBadgeText({ text: '' });
                    }, 2000);
                } else {
                    chrome.notifications.getPermissionLevel((level) => {
                        if (level === 'denied') {
                            console.warn('[GHP] Notification permission denied');
                            return;
                        }

                        try {
                            chrome.notifications.create({
                                type: 'basic',
                                iconUrl: chrome.runtime.getURL('icon.png'),
                                title: 'Game Hub Pro',
                                message: `❌ Failed to send to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                            }, (notificationId) => {
                                if (chrome.runtime.lastError) {
                                    console.error('[GHP] Notification error:', chrome.runtime.lastError);
                                    // Fallback: try without icon
                                    chrome.notifications.create({
                                        type: 'basic',
                                        title: 'Game Hub Pro',
                                        message: `❌ Failed to send to Game Hub: ${filename.substring(0, 50)}${filename.length > 50 ? '...' : ''}`
                                    }, (fallbackId) => {
                                        if (chrome.runtime.lastError) {
                                            console.error('[GHP] Fallback notification also failed:', chrome.runtime.lastError);
                                        } else {
                                            console.log('[GHP] Fallback notification created:', fallbackId);
                                        }
                                    });
                                } else {
                                    console.log('[GHP] Manual failure notification created:', notificationId);
                                }
                            });
                        } catch (e) {
                            console.error('[GHP] Failed to create notification:', e);
                        }
                    });
                }

                sendResponse({ success: success });
            });
        return true;
    }

    if (message.action === "RESUME_BROWSER_DOWNLOAD") {
        if (message.downloadId) {
            chrome.downloads.resume(message.downloadId);
        }
        sendResponse({ success: true });
        return true;
    }

    if (message.action === "GET_FORMATS") {
        fetch(FORMATS_API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: message.url })
        })
            .then(r => r.json())
            .then(data => sendResponse(data))
            .catch(err => sendResponse({ status: "error", message: err.message }));
        return true;
    }
});

// ============================================================================
// [4] UTILITIES
// ============================================================================

async function sendToGameHub(url, referer, formatId, meta = null) {
    const userAgent = self.navigator?.userAgent || "GameHub/7.0";
    try {
        // COOKIE REMOVAL: No longer sending cookies to Python
        const payload = {
            url: url,
            referer: referer,
            format_id: formatId,
            user_agent: userAgent,
            filename: meta?.filename,
            size: meta?.fileSize || meta?.size,
            reason: meta?.reason || "Unknown"
        };

        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Connection': 'close' },
            body: JSON.stringify(payload)
        });
        return response.ok;
    } catch (err) {
        console.error(`[GHP] Send to app failed: ${err}`);
        return false;
    }
}

// ============================================================================
// [5] CONTEXT MENU
// ============================================================================
chrome.runtime.onInstalled.addListener(() => {
    chrome.contextMenus.create({
        id: "gh-download-menu",
        title: "Download with Game Hub",
        contexts: ["link", "video", "audio", "image"]
    });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "gh-download-menu") {
        const url = info.linkUrl || info.srcUrl;
        if (url) sendToGameHub(url, tab.url, null, { filename: "Context Menu Download" });
    }
});