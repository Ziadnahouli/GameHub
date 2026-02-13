/**
 * content.js - Game Hub Pro Global Interceptor
 * Version: 6.4 (Privacy & Blob Protection)
 */

(() => {
    if (window.__GHP_ENGINE_LOADED__) return;
    window.__GHP_ENGINE_LOADED__ = true;

    // ============================================================================
    // [1] PRIVACY & CONFIGURATION
    // ============================================================================
    const SENSITIVE_DOMAINS = ["paypal.com", "stripe.com", "bank", "login", "auth", "checkout", "visa.com", "mastercard"];
    const ALLOWED_EXTENSIONS = Object.freeze(['.exe', '.msi', '.zip', '.rar', '.7z', '.iso', '.bin', '.mp4', '.mkv', '.mp3']);
    const KEYWORD_REGEX = /(api\/download|download-handler|get-setup|SteamSetup|EpicInstaller|EpicGamesLauncher)/i;
    const YOUTUBE_REGEX = /youtube\.com|youtu\.be/i;

    function isSensitive() {
        const host = window.location.hostname.toLowerCase();
        return SENSITIVE_DOMAINS.some(d => host.includes(d));
    }

    // ============================================================================
    // [2] DOM READY GUARD
    // ============================================================================
    function runWhenReady(fn) {
        if (document.body && document.head) return fn();
        const observer = new MutationObserver(() => { if (document.body && document.head) { observer.disconnect(); fn(); } });
        observer.observe(document.documentElement, { childList: true, subtree: true });
    }

    // ============================================================================
    // [3] UI SUBSYSTEM
    // ============================================================================
    function showConfirmationUI(url, downloadId = null) {
        if (isSensitive()) return; // Protection A1
        if (url.startsWith('blob:')) {
            console.log("GHP: Blob detected, letting browser handle naturally.");
            return; // Protection A3
        }

        if (document.querySelector('.ghp-master-modal')) return;

        let handled = false;
        const modal = document.createElement('div');
        modal.className = 'ghp-master-modal';

        Object.assign(modal.style, {
            position: 'fixed', top: '30px', right: '30px', width: '340px',
            background: 'linear-gradient(135deg, #1a1a24 0%, #0f0f17 100%)',
            border: '1px solid rgba(88, 101, 242, 0.3)',
            borderRadius: '20px',
            padding: '24px',
            zIndex: '2147483647',
            boxShadow: '0 25px 60px rgba(0,0,0,0.9), 0 0 0 1px rgba(88, 101, 242, 0.1)',
            color: 'white',
            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',
            backdropFilter: 'blur(20px)',
            animation: 'ghp-slideIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1)'
        });

        let filename = (url.split('/').pop().split('?')[0] || "Installer");

        modal.innerHTML = `
            <div class="ghp-modal-content">
                <div style="display:flex; align-items:center; gap:10px; margin-bottom:16px;">
                    <div style="width:40px; height:40px; background:linear-gradient(135deg, #5865f2 0%, #4752c4 100%); border-radius:12px; display:flex; align-items:center; justify-content:center; font-size:20px; box-shadow:0 4px 15px rgba(88, 101, 242, 0.4);">🚀</div>
                    <div>
                        <div style="font-weight:800; font-size:16px; color:#fff; letter-spacing:-0.5px; margin-bottom:2px;">GAME HUB PRO</div>
                        <div style="font-size:10px; color:rgba(255,255,255,0.5); text-transform:uppercase; letter-spacing:1px;">Download Manager</div>
                    </div>
                </div>
                <div style="font-size:12px; color:rgba(255,255,255,0.7); margin-bottom:18px; word-break:break-all; background:rgba(255,255,255,0.03); padding:12px; border-radius:10px; border:1px solid rgba(255,255,255,0.05); line-height:1.5;">
                    <div style="color:rgba(255,255,255,0.4); font-size:10px; margin-bottom:4px; text-transform:uppercase; letter-spacing:0.5px;">File</div>
                    ${decodeURIComponent(filename).substring(0, 50)}
                </div>
                <div style="display:flex; gap:10px;">
                    <button class="ghp-btn-confirm" style="flex:1.5; background:linear-gradient(135deg, #5865f2 0%, #4752c4 100%); border:none; color:white; padding:12px 20px; border-radius:10px; font-weight:700; font-size:12px; cursor:pointer; text-transform:uppercase; letter-spacing:0.5px; box-shadow:0 4px 15px rgba(88, 101, 242, 0.4); transition:all 0.2s ease;">SEND TO APP</button>
                    <button class="ghp-btn-cancel" style="flex:1; background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.1); color:rgba(255,255,255,0.8); padding:12px 16px; border-radius:10px; cursor:pointer; font-weight:600; font-size:12px; transition:all 0.2s ease;">BROWSER</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        const executeAction = (toApp) => {
            if (handled) return;
            const contentBox = modal.querySelector('.ghp-modal-content');

            if (toApp) {
                contentBox.innerHTML = `
                    <div style="text-align:center; padding: 30px 20px;">
                        <div style="width:50px; height:50px; margin:0 auto 15px; border:3px solid rgba(88, 101, 242, 0.3); border-top-color:#5865f2; border-radius:50%; animation:ghp-spin 0.8s linear infinite;"></div>
                        <div style="font-weight:700; font-size:14px; color:#fff; margin-bottom:4px;">Sending to App...</div>
                        <div style="font-size:11px; color:rgba(255,255,255,0.5);">Please wait</div>
                    </div>
                `;

                chrome.runtime.sendMessage({
                    action: "send_to_app",
                    url: url,
                    downloadId: downloadId,
                    source: "content_script"
                }, (res) => {
                    if (res && res.success !== false) {
                        contentBox.innerHTML = `
                            <div style="text-align:center; padding: 30px 20px;">
                                <div style="width:60px; height:60px; margin:0 auto 15px; background:linear-gradient(135deg, #2ecc71 0%, #27ae60 100%); border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:30px; box-shadow:0 4px 20px rgba(46, 204, 113, 0.4); animation:ghp-scaleIn 0.3s ease;">✅</div>
                                <div style="font-weight:800; font-size:16px; color:#2ecc71; margin-bottom:4px; text-transform:uppercase; letter-spacing:1px;">SUCCESS</div>
                                <div style="font-size:11px; color:rgba(255,255,255,0.5);">File sent to Game Hub</div>
                            </div>
                        `;
                        setTimeout(() => { modal.style.animation = 'ghp-fadeOut 0.3s ease'; setTimeout(() => { modal.remove(); handled = true; }, 300); }, 2000);
                    } else {
                        modal.style.animation = 'ghp-fadeOut 0.3s ease';
                        setTimeout(() => {
                            modal.remove();
                            handled = true;
                            if (downloadId) chrome.runtime.sendMessage({ action: "RESUME_BROWSER_DOWNLOAD", url, downloadId });
                            else window.location.href = url;
                        }, 300);
                    }
                });
            } else {
                modal.remove();
                handled = true;
                if (downloadId) chrome.runtime.sendMessage({ action: "RESUME_BROWSER_DOWNLOAD", url, downloadId });
                else window.location.href = url;
            }
        };

        modal.querySelector('.ghp-btn-confirm').onclick = () => executeAction(true);
        modal.querySelector('.ghp-btn-cancel').onclick = () => executeAction(false);
    }

    // ============================================================================
    // [4] VIDEO GRABBER ENGINE
    // ============================================================================
    const videoUIMap = new Map();
    let scanQueued = false;

    function scheduleScan() {
        if (scanQueued) return;
        scanQueued = true;
        requestAnimationFrame(() => {
            scanQueued = false;
            performVideoScan();
        });
    }

    function performVideoScan() {
        if (isSensitive()) return;
        const videos = document.querySelectorAll('video');

        for (const [v, bar] of videoUIMap.entries()) {
            if (!document.body.contains(v)) { bar.remove(); videoUIMap.delete(v); }
        }

        videos.forEach(v => {
            const isYT = YOUTUBE_REGEX.test(location.hostname);
            const isMainYT = isYT ? v.classList.contains('html5-main-video') : true;

            if (v.offsetWidth < 200 || v.dataset.ghpHidden || !isMainYT) return;
            if (!videoUIMap.has(v)) injectVideoUI(v);
            else syncUIPosition(v);
        });
    }

    function injectVideoUI(v) {
        const bar = document.createElement('div');
        bar.className = 'ghp-video-bar';
        bar.innerHTML = `
            <div class="ghp-v-main">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                <span class="ghp-v-text">Download Video</span>
            </div>
            <div class="ghp-v-res-menu" style="display:none;"></div>
            <div class="ghp-v-close">×</div>
        `;
        document.body.appendChild(bar);
        videoUIMap.set(v, bar);

        const mainBtn = bar.querySelector('.ghp-v-main');
        const menu = bar.querySelector('.ghp-v-res-menu');
        const txt = bar.querySelector('.ghp-v-text');

        mainBtn.onclick = (e) => {
            e.stopPropagation();
            const url = window.location.href;
            if (!YOUTUBE_REGEX.test(location.hostname)) { showConfirmationUI(v.src || url); return; }

            if (menu.style.display === 'flex') { menu.style.display = 'none'; return; }

            txt.innerText = "Scanning...";
            mainBtn.style.opacity = '0.7';
            chrome.runtime.sendMessage({ action: "GET_FORMATS", url }, (res) => {
                mainBtn.style.opacity = '1';
                if (res && res.status === "success") {
                    menu.innerHTML = res.formats.map(f => `<div class="ghp-res-opt" data-id="${f.id}">${f.label}</div>`).join('');
                    menu.style.display = 'flex';
                    txt.innerText = "Select Quality";
                    menu.querySelectorAll('.ghp-res-opt').forEach(opt => {
                        opt.onclick = (ev) => {
                            ev.stopPropagation();
                            chrome.runtime.sendMessage({ action: "send_to_app", url, format_id: opt.dataset.id });
                            menu.style.display = 'none';
                            txt.innerText = "✓ Sent!";
                            mainBtn.style.background = 'linear-gradient(135deg, #2ecc71 0%, #27ae60 100%)';
                            setTimeout(() => {
                                txt.innerText = "Download Video";
                                mainBtn.style.background = '';
                            }, 2000);
                        };
                    });
                } else {
                    txt.innerText = "Error";
                    mainBtn.style.background = 'linear-gradient(135deg, #e74c3c 0%, #c0392b 100%)';
                    setTimeout(() => {
                        txt.innerText = "Download Video";
                        mainBtn.style.background = '';
                    }, 2000);
                }
            });
        };

        bar.querySelector('.ghp-v-close').onclick = (e) => { e.stopPropagation(); bar.remove(); v.dataset.ghpHidden = "true"; };
        syncUIPosition(v);
    }

    function syncUIPosition(v) {
        const bar = videoUIMap.get(v);
        if (!bar) return;
        const rect = v.getBoundingClientRect();
        if (rect.width === 0 || rect.top > window.innerHeight || rect.bottom < 0) { bar.style.display = 'none'; return; }
        bar.style.display = 'flex';
        bar.style.top = (rect.top + window.scrollY + 15) + 'px';
        bar.style.left = (rect.left + window.scrollX + 15) + 'px';
    }

    // ============================================================================
    // [5] INITIALIZATION & LISTENERS
    // ============================================================================
    runWhenReady(() => {
        const style = document.createElement('style');
        style.textContent = `
            @keyframes ghp-slideIn {
                from { transform: translateX(400px) scale(0.9); opacity: 0; }
                to { transform: translateX(0) scale(1); opacity: 1; }
            }
            @keyframes ghp-fadeOut {
                from { transform: scale(1); opacity: 1; }
                to { transform: scale(0.9); opacity: 0; }
            }
            @keyframes ghp-spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            @keyframes ghp-scaleIn {
                from { transform: scale(0); }
                to { transform: scale(1); }
            }
            .ghp-master-modal .ghp-btn-confirm:hover {
                background: linear-gradient(135deg, #4752c4 0%, #5865f2 100%) !important;
                transform: translateY(-2px);
                box-shadow: 0 6px 20px rgba(88, 101, 242, 0.5) !important;
            }
            .ghp-master-modal .ghp-btn-confirm:active {
                transform: translateY(0);
            }
            .ghp-master-modal .ghp-btn-cancel:hover {
                background: rgba(255,255,255,0.12) !important;
                border-color: rgba(255,255,255,0.2) !important;
                color: #fff !important;
            }
            .ghp-video-bar {
                position: absolute !important;
                z-index: 2147483647;
                background: linear-gradient(135deg, #1a1a24 0%, #0f0f17 100%);
                border: 1px solid rgba(88, 101, 242, 0.3);
                border-radius: 12px;
                box-shadow: 0 10px 30px rgba(0,0,0,0.8), 0 0 0 1px rgba(88, 101, 242, 0.1);
                display: flex;
                align-items: center;
                overflow: visible;
                pointer-events: auto;
                backdrop-filter: blur(10px);
            }
            .ghp-v-main {
                padding: 10px 18px;
                color: white;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                font-size: 11px;
                font-weight: 800;
                cursor: pointer;
                border-right: 1px solid rgba(255,255,255,0.08);
                text-transform: uppercase;
                letter-spacing: 0.5px;
                transition: all 0.2s ease;
                display: flex;
                align-items: center;
                gap: 8px;
            }
            .ghp-v-main:hover {
                background: linear-gradient(135deg, #5865f2 0%, #4752c4 100%);
                transform: translateX(2px);
            }
            .ghp-v-main svg {
                width: 14px;
                height: 14px;
            }
            .ghp-v-close {
                padding: 0 12px;
                color: rgba(255,255,255,0.4);
                cursor: pointer;
                font-size: 20px;
                line-height: 1;
                transition: all 0.2s ease;
                font-weight: 300;
            }
            .ghp-v-close:hover {
                color: #ff3f34;
                transform: scale(1.2);
            }
            .ghp-v-res-menu {
                position: absolute;
                top: 48px;
                left: 0;
                background: linear-gradient(135deg, #1a1a24 0%, #0f0f17 100%);
                border: 1px solid rgba(88, 101, 242, 0.3);
                border-radius: 12px;
                width: 220px;
                display: none;
                flex-direction: column;
                padding: 8px;
                box-shadow: 0 15px 40px rgba(0,0,0,0.9);
                backdrop-filter: blur(10px);
                animation: ghp-slideIn 0.2s ease;
            }
            .ghp-res-opt {
                padding: 10px 14px;
                color: rgba(255,255,255,0.8);
                font-size: 12px;
                cursor: pointer;
                border-radius: 8px;
                transition: all 0.2s ease;
                font-weight: 500;
                margin: 2px 0;
            }
            .ghp-res-opt:hover {
                background: linear-gradient(135deg, rgba(88, 101, 242, 0.2) 0%, rgba(88, 101, 242, 0.1) 100%);
                color: #5865f2;
                transform: translateX(4px);
            }
        `;
        document.head.appendChild(style);

        document.addEventListener('click', (e) => {
            if (isSensitive()) return;
            const link = e.target.closest('a');
            if (!link || !link.href || link.href.startsWith(location.href + "#")) return;
            const url = String(link.href);
            if (ALLOWED_EXTENSIONS.some(ext => url.toLowerCase().includes(ext)) || link.hasAttribute('download') || KEYWORD_REGEX.test(url)) {
                if (link.target === "_blank") link.target = "_self";
                e.preventDefault(); e.stopPropagation();
                showConfirmationUI(url);
            }
        }, true);

        chrome.runtime.onMessage.addListener((req) => {
            if (req.action === "SHOW_MODAL_REMOTE" && !isSensitive()) showConfirmationUI(req.url, req.downloadId);
        });

        const observer = new MutationObserver(scheduleScan);
        observer.observe(document.body, { childList: true, subtree: true });
        window.addEventListener('scroll', scheduleScan, { passive: true });
        window.addEventListener('resize', scheduleScan, { passive: true });
        scheduleScan();
    });

})();