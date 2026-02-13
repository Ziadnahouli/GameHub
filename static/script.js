/**
 * ============================================================================
 *                                GAME HUB PRO
 *                        MASTER UI CONTROL INTERFACE
 * ============================================================================
 * Version: 4.8.5 (Full Production Source)
 * Built for: Windows Desktop Environment
 * Modules: Library, IDM Pro, Extension Bridge, System Monitor, Theme Engine
 * ----------------------------------------------------------------------------
 * This file orchestrates all frontend interactions and maintains a persistent
 * link with the Python backend via Socket.io and RESTful APIs.
 * ============================================================================
 */

/**
 * [1] GLOBAL APPLICATION SCOPE
 * maintains the 'Source of Truth' for the UI session.
 */

/** @type {Array<Object>} Stores the full game metadata objects from the DB */
let allGames = [];

/** @type {Object|null} The specific game object currently focused in the Detail Panel */
let currentGame = null;

/** @type {string} Defines the layout for the library view ('grid' or 'list') */
let currentView = 'grid';

/** @type {Object|null} Holds telemetry about the currently running game executable */
let activeGameData = null;

/** @type {boolean} Flag to pause background polling during file synchronization */
let isUpdatingExtension = false;

/** @type {Object} The Socket.io instance reference */
let socket = null;

/** @type {string} Target version string for Extension compliance */
const REQUIRED_EXT_VERSION = "6.4";

/** 
 * Bootstrap Modal References 
 * These allow programmatic control (show/hide) of UI windows.
 */
let addGameModal = null;
let settingsModal = null;
let controllerSettingsModal = null;
let friendsModal = null;
let gamePropsModal = null;
let upgradeModal = null;

/**
 * [2] THEME CONFIGURATION ENGINE
 * Defines the visual identity of the application.
 */
const themes = {
    "default": {
        "name": "Blurple (Standard)",
        "vars": {
            "--bg-dark": "#0b0f19",
            "--text-main": "#ffffff",
            "--text-muted": "#a1a1aa",
            "--accent": "#5865f2",
            "--accent-glow": "rgba(88, 101, 242, 0.4)",
            "--play-green": "#2ea043"
        }
    },
    "midnight": {
        "name": "Midnight Black",
        "vars": {
            "--bg-dark": "#000000",
            "--text-main": "#e2e8f0",
            "--text-muted": "#64748b",
            "--accent": "#38bdf8",
            "--accent-glow": "rgba(56, 189, 248, 0.5)",
            "--play-green": "#0284c7"
        }
    },
    "cyberpunk": {
        "name": "Neon Cyberpunk",
        "vars": {
            "--bg-dark": "#0f0518",
            "--text-main": "#ffffff",
            "--text-muted": "#00d2d3",
            "--accent": "#ff0099",
            "--accent-glow": "rgba(255, 0, 153, 0.8)",
            "--play-green": "#f1c40f"
        }
    },
    "christmas": {
        "name": "Winter Holiday",
        "vars": {
            "--bg-dark": "#0b0f19",
            "--text-main": "#ffffff",
            "--text-muted": "#b0b0b0",
            "--accent": "#ff3f34",
            "--accent-glow": "rgba(255, 63, 52, 0.6)",
            "--play-green": "#0daeb0"
        }
    }
};

/**
 * Applies CSS variable mapping to the document root based on theme selection.
 * Also handles environmental VFX like snow containers.
 * 
 * @param {string} themeName - Key for the themes object.
 */
function applyTheme(themeName) {
    console.log(`[ThemeEngine] Initializing switch to: ${themeName}`);

    const theme = themes[themeName] || themes["default"];
    const root = document.documentElement;

    // Iterate through CSS variable map
    for (const [key, value] of Object.entries(theme.vars)) {
        root.style.setProperty(key, value);
    }

    // Manage Visual Effects Overlays
    const snowContainer = document.querySelector('.snow-container');
    const groundSnow = document.querySelector('.ground-snow');
    const sidebar = document.getElementById('sidebar');

    if (themeName === 'christmas') {
        if (snowContainer) snowContainer.style.display = 'block';
        if (groundSnow) groundSnow.style.display = 'block';
        if (sidebar) sidebar.classList.add('christmas-lights');
    } else {
        if (snowContainer) snowContainer.style.display = 'none';
        if (groundSnow) groundSnow.style.display = 'none';
        if (sidebar) sidebar.classList.remove('christmas-lights');
    }
}

/**
 * [3] CORE INITIALIZATION PIPELINE
 * Manages the sequential loading of all application sub-systems.
 */

window.addEventListener('DOMContentLoaded', async () => {
    // Console Branding
    console.log("%c 🚀 GAME HUB MASTER ENGINE ONLINE ", "background: #18181b; color: #5865f2; font-weight: bold; font-size: 14px; padding: 8px; border: 1px solid #5865f2;");

    try {
        // [A] Networking Initialization
        console.log("[Init] Establishing Socket.io Handshake...");
        socket = io.connect(window.location.origin);
        setupSocketHandlers();

        // [B] UI Component Mapping
        console.log("[Init] Mapping Bootstrap Modal Controllers...");
        addGameModal = new bootstrap.Modal(document.getElementById('addGameModal'));
        settingsModal = new bootstrap.Modal(document.getElementById('settingsModal'));
        controllerSettingsModal = new bootstrap.Modal(document.getElementById('controllerSettingsModal'));
        friendsModal = new bootstrap.Modal(document.getElementById('friendsModal'));
        gamePropsModal = new bootstrap.Modal(document.getElementById('gamePropsModal'));

        // [C] Configuration Loading
        console.log("[Init] Fetching Backend Configuration...");
        const settingsRes = await fetch('/api/settings');
        const configData = await settingsRes.json();
        if (configData.theme) applyTheme(configData.theme);

        // [D] Event Registration
        console.log("[Init] Binding DOM Event Listeners...");
        setupEventListeners();

        // [E] Startup Background Workers
        console.log("[Init] Launching Background Polling Tasks...");
        runStartupSequence();

        console.log("[Init] System fully initialized and ready.");
    } catch (criticalError) {
        console.error("[Init] Fatal engine failure during startup:", criticalError);
        showToast("System failed to initialize properly. Check backend connection.", "danger");
    }
});

/**
 * Wrapper for the specific background checks required at app launch.
 */
/**
 * MASTER STARTUP SEQUENCE
 * Executed after the UI is drawn. Handles background syncing and 
 * update checks without blocking user interaction.
 */
/**
 * MASTER STARTUP SEQUENCE
 * Fixed: Now correctly triggers the /api/library/fetch_covers endpoint.
 */
async function runStartupSequence() {
    console.log("[Startup] Initializing background pipelines...");

    // 1. Load the Library
    await fetchGamesAndRender();

    // 2. Start Core Systems
    checkActiveGame();
    restoreDownloads();
    startSystemMonitor();

    // 3. Check extension status on startup (with delay to allow heartbeat)
    // Delay matches grace period to prevent false "not installed" locks
    setTimeout(() => {
        checkExtensionStatus();
    }, 6000);


    // 4. TRIGGER COVER SYNC (Fix)
    // We check if any game is missing a local cached image
    const needsCovers = allGames.some(game => !game.grid_image_url || !game.grid_image_url.includes('/api/covers/'));

    if (needsCovers) {
        console.log("[Startup] Library requires local caching. Sending trigger...");
        // This is the CRITICAL fix: Call our new Python route
        fetch('/api/library/fetch_covers', { method: 'POST' });
    }

    // 5. Extension & App Updates
    checkForExtensionCloudUpdate(); // Check for bridge updates
    setTimeout(checkForAppUpdates, 3000);
}

/**
 * [4] WEBSOCKET SIGNAL PROCESSING
 * Handles real-time events pushed from the Python Core.
 */

function setupSocketHandlers() {
    if (!socket) return;

    socket.on('connect', () => {
        console.log("[Socket] Connection verified. Ready for stream.");
    });

    // Handle library refresh triggers
    socket.on('scan_complete', () => {
        console.log("[Socket] Remote scan signal received.");
        showScanOverlay(false);
        fetchGamesAndRender();
    });

    socket.on('library_updated', () => {
        console.log("[Socket] Metadata updated. Refreshing UI...");
        fetchGamesAndRender(); // This will redraw the grid with the new images
    });

    // Handle App Updater progress bar
    socket.on('update_progress', (data) => {
        const updateBtn = document.getElementById('update-btn');
        if (updateBtn) {
            updateBtn.innerHTML = `<i class="fas fa-circle-notch fa-spin me-2"></i> ${data.status} (${data.percent}%)`;
        }
    });

    // Handle Metadata changes (Cover art, titles)
    socket.on('game_updated', (updatedGame) => {
        const index = allGames.findIndex(g => g.name === updatedGame.name && g.source === updatedGame.source);
        if (index !== -1) {
            allGames[index] = updatedGame;
        }
        if (currentGame && currentGame.name === updatedGame.name) {
            showGameDetails(updatedGame);
        }
    });

    // Handle Process Activation (Game Launch)
    socket.on('game_started', (data) => {
        console.log(`[Socket] New active process: ${data.name}`);
        activeGameData = data;
        clearTimeout(launchTimer);

        // Auto-close launching overlay
        const launchingModalEl = document.getElementById('launchingModal');
        const inst = bootstrap.Modal.getInstance(launchingModalEl);
        if (inst) inst.hide();

        if (currentGame && currentGame.name === data.name) {
            showGameDetails(currentGame);
        }
    });

    // Handle Process Termination
    socket.on('game_stopped', () => {
        console.log("[Socket] Process terminated. Updating metrics.");
        activeGameData = null;
        fetchGamesAndRender(); // Update playtime display immediately
        if (currentGame) showGameDetails(currentGame);
    });

    // High-Frequency Download Telemetry
    socket.on('download_update', (data) => {
        updateDownloadUI(data);

        // If this is a new download starting, bring window to front
        if (data.status === 'Queued' || data.status === 'Resolving' || data.status === 'Downloading') {
            // Request window focus from backend
            fetch('/api/window/focus', { method: 'POST' }).catch(() => { });
        }
    });

    // Optimizer Telemetry
    socket.on('optimizer_update', (data) => {
        // data matches strict schema
        optimizerBenchmarkData = data;
        if (currentGame && currentGame.name && data.unique_id.includes(currentGame.name)) {
            renderOptimizerPanel(currentGame);
        }
    });
}

/**
 * [5] STATIC INTERFACE BINDING
 * Sets up the logic for all persistent sidebar and navigation elements.
 */

function setupEventListeners() {
    /**
     * TOP BAR COMPONENTS
     */
    const refreshBtn = document.getElementById('refresh-btn');
    if (refreshBtn) refreshBtn.onclick = () => refreshLibrary(false);

    const viewToggle = document.getElementById('view-toggle-btn');
    if (viewToggle) viewToggle.onclick = toggleView;

    const searchInp = document.getElementById('search-input');
    if (searchInp) searchInp.oninput = displayCurrentView;

    /**
     * SIDEBAR COMPONENTS
     */
    const settingsBtn = document.getElementById('settings-btn');
    if (settingsBtn) settingsBtn.onclick = openSettings;

    const addBtn = document.getElementById('add-game-btn');
    if (addBtn) addBtn.onclick = () => addGameModal.show();

    const friendsBtn = document.getElementById('global-friends-btn');
    if (friendsBtn) {
        friendsBtn.onclick = () => {
            friendsModal.show();
            loadFriends();
        };
    }

    const navDownloads = document.getElementById('nav-downloads');
    if (navDownloads) {
        navDownloads.onclick = () => {
            switchMainView('downloads');
            checkExtensionStatus();
        };
    }


    /**
     * GAME DOCK COMPONENTS
     */
    document.getElementById('play-button').onclick = launchCurrentGame;
    document.getElementById('favorite-btn').onclick = toggleFavorite;
    document.getElementById('hide-btn').onclick = toggleHidden;
    document.getElementById('folder-btn').onclick = openGameFolder;
    document.getElementById('change-cover-btn').onclick = changeCover;
    document.getElementById('game-props-btn').onclick = openGameProps;
    document.getElementById('delete-game-btn').onclick = deleteCurrentGame;

    const bridgeBtn = document.getElementById('auto-bridge-btn');
    if (bridgeBtn) bridgeBtn.onclick = toggleAutoBridge;

    /**
     * MODAL SUBMIT BUTTONS
     */
    document.getElementById('browse-btn').onclick = browseForGame;
    document.getElementById('save-game-btn').onclick = saveManualGame;
    document.getElementById('save-settings-btn').onclick = saveSettings;
    document.getElementById('save-game-props-btn').onclick = saveGameProps;
    document.getElementById('bridge-settings-btn').onclick = openControllerSettings;
    document.getElementById('save-controller-settings-btn').onclick = saveControllerSettings;

    const extReload = document.getElementById('ext-reload-btn');
    if (extReload) extReload.onclick = performExtensionUpdate;

    const bridgeSwitch = document.getElementById('bridgeToggle');
    if (bridgeSwitch) bridgeSwitch.onchange = toggleBridgeHardware;
}

/**
 * [6] NAVIGATION MANAGEMENT
     * Switches primary UI view containers.
     */

/**
 * Switches the main interface between the Library and the Downloader.
 * 
 * @param {string} viewName - Target view ID ('games' or 'downloads').
 */
function switchMainView(viewName) {
    const gamesView = document.getElementById('games-view-container');
    const dlsView = document.getElementById('downloads-view-container');
    const sidebarDls = document.getElementById('nav-downloads');

    // Reset all
    if (gamesView) gamesView.style.setProperty('display', 'none', 'important');
    if (dlsView) dlsView.style.setProperty('display', 'none', 'important');

    if (sidebarDls) sidebarDls.classList.remove('active');
    document.querySelectorAll('#library-list .nav-link').forEach(link => link.classList.remove('active'));

    if (viewName === 'downloads') {
        if (dlsView) dlsView.style.setProperty('display', 'block', 'important');
        if (sidebarDls) sidebarDls.classList.add('active');
        checkExtensionStatus();
    } else {
        if (gamesView) gamesView.style.setProperty('display', 'flex', 'important');
    }
}

/**
 * Cycles through available display modes for the game library.
 */
function toggleView() {
    currentView = (currentView === 'grid') ? 'list' : 'grid';
    const toggleIcon = document.querySelector('#view-toggle-btn i');

    // Update visual icon
    if (toggleIcon) {
        toggleIcon.className = (currentView === 'grid') ? 'fas fa-table-cells-large' : 'fas fa-list';
    }

    displayCurrentView();
}

/**
 * [7] LIBRARY RENDERING ENGINE
 * Manages the drawing and filtering of game cards.
 */

/**
 * Synchronizes local state with the Python Database.
 */
/**
 * Synchronizes local state with the Python Database.
 * Optimized with LocalStorage Caching for instant UI rendering on launch.
 */
async function fetchGamesAndRender() {
    console.log("[Library] Initializing synchronization...");

    // 1. INSTANT UI RESTORATION (The "Speed" Secret)
    // We check the browser's local storage for a saved version of the library.
    const cachedLibrary = localStorage.getItem('ghp_library_cache');

    if (cachedLibrary) {
        try {
            allGames = JSON.parse(cachedLibrary);

            // Draw the sidebar and grid immediately using cached data
            rebuildSidebarSources();
            displayCurrentView();

            console.log(`[Cache] Restored ${allGames.length} items from local storage instantly.`);
        } catch (cacheErr) {
            console.warn("[Cache] Corrupted cache detected. Clearing...");
            localStorage.removeItem('ghp_library_cache');
        }
    }

    // 2. BACKGROUND SERVER SYNC
    // While the user is already looking at the cached UI, we ping the server for updates.
    try {
        const response = await fetch('/api/games');

        if (response.ok) {
            const freshData = await response.json();

            // Update our global state
            allGames = freshData;

            // Update the cache so the NEXT launch is also fast
            localStorage.setItem('ghp_library_cache', JSON.stringify(freshData));

            // Re-render only if something actually changed or if cache was empty
            rebuildSidebarSources();
            displayCurrentView();

            console.log("[Sync] Library synchronized with Python backend.");
        } else {
            throw new Error("Server responded with error status.");
        }
    } catch (e) {
        console.error("[Sync] Could not reach backend. Operating in Offline/Cache mode.");
        // We don't show an error toast here because the user can still see the cached games.
    }
}

/**
 * Dynamically builds the platform navigation in the sidebar based on library data.
 */
function rebuildSidebarSources() {
    const list = document.getElementById('library-list');
    if (!list) return;

    // Persist active state across rebuilds
    const lastActiveSource = list.querySelector('.active')?.dataset.source || 'All Games';
    list.innerHTML = '';

    // Base Categories
    let sources = ['All Games', 'Favorites'];

    // Append dynamically discovered sources (Steam, Epic, EA, etc)
    const platformSet = [...new Set(allGames.map(game => game.source))].sort();
    sources = sources.concat(platformSet);

    // Append Hidden bin
    sources.push('Hidden');

    sources.forEach(source => {
        const navItem = document.createElement('li');
        navItem.className = 'nav-item';

        const isActive = (source === lastActiveSource);

        navItem.innerHTML = `
            <a class="nav-link ${isActive ? 'active' : ''}" data-source="${source}" href="#">
                <span>${source}</span>
            </a>
        `;

        navItem.onclick = (e) => {
            e.preventDefault();
            switchMainView('games');

            // UI Toggle Logic
            list.querySelectorAll('.active').forEach(a => a.classList.remove('active'));
            navItem.querySelector('a').classList.add('active');

            displayCurrentView();
        };
        list.appendChild(navItem);
    });
}

/**
 * Calculates current filters and renders the Grid or List containers.
 * Includes defensive logic for missing cover art.
 */
// script.js

/**
 * THE MASTER LIBRARY RENDERER
 * Handles Grid/List switching, Filtering, Sorting, and Image Fallbacks.
 */
/**
 * THE MASTER LIBRARY RENDERER
 * Optimized for local-first image loading and smooth UI transitions.
 */
function displayCurrentView() {
    const gridEl = document.getElementById('grid-view');
    const listEl = document.getElementById('list-view');
    const searchEl = document.getElementById('search-input');

    if (!gridEl || !listEl) return;

    // 1. Get current filters
    const activeNav = document.querySelector('#library-list .nav-link.active');
    const activePlatform = activeNav ? activeNav.dataset.source : 'All Games';
    const searchQuery = searchEl ? searchEl.value.toLowerCase() : '';

    // 2. Perform filtering and sorting
    let itemsToRender = allGames.filter(game => {
        const matchesSearch = game.name.toLowerCase().includes(searchQuery);
        if (!matchesSearch) return false;

        if (activePlatform === 'All Games') return !game.hidden;
        if (activePlatform === 'Favorites') return game.favorite && !game.hidden;
        if (activePlatform === 'Hidden') return game.hidden;

        return game.source === activePlatform && !game.hidden;
    });

    itemsToRender.sort((a, b) => a.name.localeCompare(b.name));

    // 3. UI Toggle Logic
    gridEl.style.display = (currentView === 'grid') ? 'grid' : 'none';
    listEl.style.display = (currentView === 'grid') ? 'none' : 'block';

    gridEl.innerHTML = '';
    listEl.innerHTML = '';

    // 4. Render Execution
    itemsToRender.forEach(game => {
        // --- PREPARE IMAGE PATH ---
        let displayImg = game.grid_image_url;

        // Fallback: If local cache isn't ready but it's a Steam game, use live header
        if ((!displayImg || displayImg === "" || displayImg === "MISSING") && game.source === "Steam") {
            displayImg = `https://steamcdn-a.akamaihd.net/steam/apps/${game.launch_id}/header.jpg`;
        }

        // --- RENDER LIST ITEM ---
        const listItem = document.createElement('a');
        listItem.className = 'list-group-item list-group-item-action bg-transparent text-white border-0 py-3 pointer';
        listItem.innerHTML = `<i class="fas fa-play-circle me-3 opacity-40"></i> ${game.name}`;
        listItem.onclick = () => showGameDetails(game);
        listEl.appendChild(listItem);

        // --- RENDER GRID CARD ---
        const card = document.createElement('div');
        card.className = 'grid-item';


        if (displayImg && displayImg !== "MISSING") {
            // Smooth Fade-In Logic using onload
            card.innerHTML = `
                <img src="${displayImg}" loading="lazy" 
                     style="opacity: 0; transition: opacity 0.5s ease-out;"
                     onload="this.style.opacity='1'"
                     onerror="this.style.display='none'; this.parentElement.innerHTML='<div class=\\'grid-item-placeholder\\'><span>${game.name}</span></div>'">
            `;
        } else {
            card.innerHTML = `
                <div class="grid-item-placeholder"><span>${game.name}</span></div>
            `;
        }

        card.onclick = () => showGameDetails(game);
        gridEl.appendChild(card);
    });

    // 5. Persistent selection logic
    if (itemsToRender.length > 0 && !currentGame) {
        showGameDetails(itemsToRender[0]);
    }
}

/**
 * [8] GAME PROPERTY ACTIONS
 * Functions for modifying game state (Favorites, Hidden, Paths, Art).
 */


/**
 * Updates the Right Sidebar detail panel with full metadata.
 * Handles the 'Hero Background' blur effect.
 * 
 * @param {Object} game - Data object for the game to display.
 */
function showGameDetails(game) {
    if (!game) return;
    currentGame = game;

    // 1. Textual Mapping
    document.getElementById('game-title').textContent = game.name;
    document.getElementById('last-played').textContent = game.last_played ? new Date(game.last_played * 1000).toLocaleDateString() : 'Never';
    document.getElementById('playtime').textContent = `${((game.playtime_seconds || 0) / 3600).toFixed(1)}h`;

    // Update source badge
    const sourceBadge = document.getElementById('source-badge');
    if (sourceBadge) {
        sourceBadge.textContent = game.source || 'Unknown';
    }

    // Update source display
    const sourceDisplay = document.getElementById('game-source-display');
    if (sourceDisplay) {
        sourceDisplay.textContent = game.source || 'Unknown';
    }


    // 2. Hero Image Logic
    let primaryImg = (game.source === 'Steam')
        ? `https://steamcdn-a.akamaihd.net/steam/apps/${game.launch_id}/header.jpg`
        : game.grid_image_url;

    const isValid = primaryImg && primaryImg !== "MISSING" && primaryImg !== "Unknown";
    document.body.style.setProperty('--active-bg', isValid ? `url('${primaryImg}')` : 'none');

    const hero = document.getElementById('hero-image-container');
    if (hero) {
        hero.innerHTML = isValid
            ? `<img src="${primaryImg}" style="width:100%; height:100%; object-fit:cover;">`
            : `<div class="h-100 w-100 d-flex align-items-center justify-content-center bg-dark"><i class="fas fa-gamepad fa-4x opacity-10"></i></div>`;
    }

    // 3. Delete Button Logic
    const deleteBtn = document.getElementById('delete-game-btn');
    if (deleteBtn) {
        // Only show for 'Other Games', hide for Steam/Epic/EA
        if (game.source === 'Other Games') {
            deleteBtn.style.display = 'block';
        } else {
            deleteBtn.style.display = 'none';
        }
    }

    // 4. Other Button States
    const favoriteBtn = document.getElementById('favorite-btn');
    if (favoriteBtn) {
        favoriteBtn.classList.toggle('favorited', !!game.favorite);
    }

    const bridgeBtn = document.getElementById('auto-bridge-btn');
    if (bridgeBtn) {
        bridgeBtn.classList.toggle('active-bridge', !!game.auto_bridge);
    }

    const playBtn = document.getElementById('play-button');
    if (playBtn) {
        if (activeGameData && activeGameData.name === game.name) {
            playBtn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i> RUNNING';
            playBtn.disabled = true;
        } else {
            playBtn.innerHTML = '<i class="fas fa-play me-2"></i> PLAY';
            playBtn.disabled = false;
        }
    }

    // 5. Render Optimizer
    if (typeof renderOptimizerPanel === 'function') {
        renderOptimizerPanel(game);
    }
}

/**
 * UPDATER LOGIC
 * Handles the manual check and trigger process for game updates.
 */


/**
 * Triggers a localized launch command to the Flask API.
 */
let launchTimer = null;
async function launchCurrentGame() {
    if (!currentGame) return;

    console.log(`[Launch] Initializing sequence for: ${currentGame.name}`);

    // 1. Setup UI Modal
    const launchingModalEl = document.getElementById('launchingModal');
    const launchingText = document.getElementById('launchingGameName');
    if (launchingText) launchingText.textContent = currentGame.name;

    const launchModal = bootstrap.Modal.getOrCreateInstance(launchingModalEl);
    launchModal.show();

    // 2. START THE 10-SECOND SAFETY TIMER
    // If the backend doesn't confirm the game started within 10s, close the window
    if (launchTimer) clearTimeout(launchTimer);
    launchTimer = setTimeout(() => {
        // Double check: Only hide if the game isn't actually active yet
        if (!activeGameData || activeGameData.name !== currentGame.name) {
            console.warn("[Launch] Game taking too long. Auto-closing modal.");
            launchModal.hide();
            showToast("Game is taking a while to start. Please check your Taskbar.", "info");
        }
    }, 10000);

    try {
        // 3. Send Launch Command to Python
        const res = await fetch('/api/launch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: currentGame.name,
                source: currentGame.source
            })
        });

        const data = await res.json();
        if (data.status === 'error') {
            // If the backend fails immediately, stop the timer and hide modal
            clearTimeout(launchTimer);
            launchModal.hide();
            showToast("Launch Error: " + data.message, "danger");
        }
    } catch (e) {
        clearTimeout(launchTimer);
        launchModal.hide();
        showToast("Backend Server Unreachable", "danger");
    }
}

/**
 * Triggers a Windows Explorer shell command for the current path.
 */
async function openGameFolder() {
    if (!currentGame || !currentGame.install_path || currentGame.install_path === "Unknown") {
        showToast("Installation directory not identified.", "warning");
        return;
    }

    try {
        await fetch('/api/open_folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: currentGame.install_path })
        });
    } catch (e) {
        showToast("FileSystem trigger failed.", "danger");
    }
}

/**
 * Requests a metadata change from the user and syncs to DB.
 */
async function changeCover() {
    if (!currentGame) return;
    const url = prompt("Enter Direct URL for Cover Image:", currentGame.grid_image_url);
    if (url === null) return;

    await writeGameMetadata({ grid_image_url: url });
    fetchGamesAndRender();
}

async function toggleFavorite() {
    if (!currentGame) return;
    const state = !currentGame.favorite;
    await writeGameMetadata({ favorite: state });
    displayCurrentView();
}

async function toggleHidden() {
    if (!currentGame) return;
    const state = !currentGame.hidden;
    await writeGameMetadata({ hidden: state });
    fetchGamesAndRender();
}

/**
 * Helper to update game data both locally and on the server.
 * 
 * @param {Object} updateMap - Partial game data to update.
 */
async function writeGameMetadata(updateMap) {
    if (!currentGame) return;
    try {
        await fetch('/api/update_game', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: currentGame.name,
                source: currentGame.source,
                update_data: updateMap
            })
        });
        // Instant local update
        Object.assign(currentGame, updateMap);
    } catch (err) {
        console.error("[Metadata] DB write failure:", err);
    }
}

/**
 * [9] IDM PRO - HIGH SPEED DOWNLOAD MANAGER
 * Orchestrates parallel threads and real-time status updates.
 */

async function startDownload() {
    const field = document.getElementById('downloadUrlInput');
    const inputUrl = field.value.trim();
    if (!inputUrl) {
        showToast("Enter a download link to begin.", "warning");
        return;
    }

    field.value = '';

    try {
        const call = await fetch('/api/downloads/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: inputUrl })
        });
        const data = await call.json();

        if (data.status === 'success') {
            showToast("Parallel segmenting started.", "success");
            switchMainView('downloads');
        } else {
            showToast(`Server rejected URL: ${data.message}`, "danger");
        }
    } catch (networkError) {
        showToast("Download engine is offline.", "danger");
    }
}

/**
 * Precision DOM Controller for Download UI.
 * This function manually targets elements to ensure no visual flickering.
 * 
 * @param {Object} data - The task telemetry object.
 */
/**
 * Precision UI Update for individual tasks.
 * Targets specific DOM elements to prevent flickering and handle YouTube resolving.
 */
function updateDownloadUI(data) {
    const list = document.getElementById('downloads-list');
    const emptyState = document.getElementById('dl-empty-state');

    if (data.status === "Cancelled") {
        const nodeToRemove = document.getElementById(`dl-${data.id}`);
        if (nodeToRemove) nodeToRemove.remove();
        if (list && list.querySelectorAll('.idm-card').length === 0) {
            if (emptyState) emptyState.style.display = 'block';
        }
        updateGlobalThroughput();
        return;
    }

    if (emptyState) emptyState.style.display = 'none';

    let item = document.getElementById(`dl-${data.id}`);

    if (!item) {
        item = document.createElement('div');
        item.id = `dl-${data.id}`;
        item.className = 'idm-card';
        item.innerHTML = `
            <div class="d-flex justify-content-between align-items-start mb-1">
                <div class="overflow-hidden">
                    <div class="d-flex align-items-center gap-2 mb-2">
                        <span class="badge-idm">${data.category}</span>
                        <span class="small dl-status-text text-muted">${data.status}</span>
                    </div>
                    <h5 class="text-white fw-bold text-truncate mb-0" style="max-width: 450px;">${data.filename}</h5>
                </div>
                <div class="text-end">
                    <div class="throughput-val fw-bold text-info mb-1" style="font-family:monospace; font-size:1.1rem;">0.00 MB/s</div>
                    <div class="small text-muted dl-size-text">Connecting...</div>
                </div>
            </div>
            <div class="idm-progress-container">
                <div class="idm-progress-bar" style="width: 0%"></div>
            </div>
            <div class="d-flex justify-content-between align-items-center">
                <div class="small text-muted fw-bold dl-percent-text">0% COMPLETED</div>
                <div class="d-flex gap-3 align-items-center">
                    <i class="fas fa-tachometer-alt text-muted pointer" title="Set Speed Limit" onclick="setTaskSpeedLimit('${data.id}')"></i>
                    <i class="fas fa-play text-muted pointer" onclick="controlDownload('${data.id}', 'resume')"></i>
                    <i class="fas fa-pause text-muted pointer" onclick="controlDownload('${data.id}', 'pause')"></i>
                    <i class="fas fa-trash-alt text-danger pointer" onclick="controlDownload('${data.id}', 'cancel')"></i>
                </div>
            </div>
        `;
        list.prepend(item);
    }

    const progressBar = item.querySelector('.idm-progress-bar');
    const speedText = item.querySelector('.throughput-val');
    const sizeText = item.querySelector('.dl-size-text');
    const percentText = item.querySelector('.dl-percent-text');
    const statusText = item.querySelector('.dl-status-text');
    const titleText = item.querySelector('h5');

    // --- FIX: Logic based strictly on Status ---
    const isResolving = data.status === "Resolving" || data.status === "Resolving Quality...";

    if (isResolving) {
        progressBar.style.width = "100%";
        progressBar.classList.add('progress-bar-striped', 'progress-bar-animated');
        progressBar.style.background = "#5865f2";
        speedText.textContent = "SEARCHING...";
        sizeText.textContent = "Fetching metadata...";
        percentText.textContent = "PREPARING ENGINE";
    } else {
        progressBar.classList.remove('progress-bar-striped', 'progress-bar-animated');
        progressBar.style.width = `${data.progress}%`;

        if (data.status === "Completed") {
            progressBar.style.background = "var(--play-green)";
            speedText.classList.replace('text-info', 'text-muted');
        } else if (data.status === "Failed" || data.status === "Error") {
            progressBar.style.background = "#ff3f34";
        } else {
            progressBar.style.background = "linear-gradient(90deg, #5865f2, #ff0099)";
        }

        speedText.textContent = data.speed;
        sizeText.textContent = `${data.downloaded} / ${data.total}`;
        percentText.textContent = `${Math.round(data.progress)}% COMPLETED`;
    }

    statusText.textContent = data.status;
    if (titleText && data.filename !== "Resolving...") {
        titleText.textContent = data.filename;
    }

    updateGlobalThroughput();
}

/**
 * Aggregates all throughput values for the primary dashboard display.
 */
function updateGlobalThroughput() {
    let combinedSpeed = 0;

    // Find every element on the screen that shows an individual speed
    const speedElements = document.querySelectorAll('.throughput-val');

    speedElements.forEach(el => {
        // Extract the number (e.g., "1.25 MB/s" -> 1.25)
        const val = parseFloat(el.textContent) || 0;
        combinedSpeed += val;
    });

    // Update the main "TOTAL THROUGHPUT" text in the top right
    const dashboardHeader = document.getElementById('total-dl-speed');
    if (dashboardHeader) {
        dashboardHeader.textContent = combinedSpeed.toFixed(2) + " MB/s";
    }
}

async function controlDownload(id, action) {
    try {
        await fetch('/api/downloads/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: id, action: action })
        });
    } catch (exc) {
        showToast("Task signaling failed.", "danger");
    }
}

async function setTaskSpeedLimit(id) {
    const limit = prompt("Enter download speed limit in MB/s (0 for unlimited):", "0");
    if (limit === null) return;

    let speedLimitVal = parseFloat(limit);
    if (isNaN(speedLimitVal) || speedLimitVal < 0) {
        showToast("Invalid speed limit value.", "warning");
        return;
    }

    // aria2 expects "0" for unlimited, or "XM" for X MB/s
    const ariaLimit = speedLimitVal === 0 ? "0" : speedLimitVal + "M";

    try {
        const response = await fetch('/api/downloads/update_options', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: id,
                options: { "max-download-limit": ariaLimit }
            })
        });
        const data = await response.json();
        if (data.status === 'success') {
            showToast(`Speed limit set to ${limit} MB/s`, "success");
        } else {
            showToast(`Failed to set limit: ${data.message}`, "danger");
        }
    } catch (err) {
        showToast("Error updating speed limit.", "danger");
    }
}

/**
 * [10] BROWSER BRIDGE & EXTENSION SECURITY
 * Manages the version locking and automated file syncing.
 */

/**
 * Validates the connection between the UI and the Browser Extension.
 * In Desktop Mode, it skips the lock to allow local file management.
 */
/**
 * checkExtensionStatus (script.js)
 * Asks the Python Relay for the last reported version.
 */
/**
 * BROWSER BRIDGE VERIFICATION (App Side)
 * 
 * This function polls the Python Backend Relay to get the version 
 * reported by the Chrome Extension. It is the core logic for the 
 * Download Manager's security gates.
 */
async function checkExtensionStatus() {
    // 1. Only run this check if the Download Manager tab is currently visible
    const dlTab = document.getElementById('downloads-view-container');
    if (!dlTab || dlTab.style.display === 'none') return;

    try {
        // 2. CACHE BUSTER: We add a unique timestamp to the URL (?t=...)
        // This forces the standalone app engine to ignore its internal cache
        // and get fresh data from the Python server every time.
        const response = await fetch(`/api/extension/get_last_version?t=${Date.now()}`);

        if (!response.ok) throw new Error("Backend relay unreachable");

        const data = await response.json();

        // 3. NORMALIZE: Clean up the strings to prevent "5.1" !== "5.1 " errors
        const installedVersion = String(data.version || "0.0").trim();
        const requiredVersion = String(REQUIRED_EXT_VERSION).trim();

        // LOGGING (Only visible if app is in debug=True mode)
        console.log(`[Bridge Check] Installed: "${installedVersion}" | Target: "${requiredVersion}"`);

        // 4. LOGIC TREE

        // Grace period: If backend is still checking, don't show lock yet
        if (installedVersion === "CHECKING" || (data.grace_period && installedVersion === "0.0")) {
            console.log("[Bridge Check] Grace period - waiting for extension heartbeat...");
            // Don't update UI, just wait
            return;
        }

        // Grace period for extension reload: Don't show version mismatch during reload
        if (data.reload_in_progress && data.grace_period) {
            console.log("[Bridge Check] Extension reload in progress - waiting for new version...");
            // Don't update UI, just wait for reload to complete
            return;
        }

        if (installedVersion === "0.0") {
            /** 
             * SCENARIO: No heartbeat received from Chrome.
             * This happens if Google Chrome is closed or the extension is uninstalled.
             * 
             * We add a small exception: If the user JUST clicked 'Refresh Bridge',
             * we stay quiet for a few seconds to let Chrome wake up.
             */
            if (isUpdatingExtension) {
                console.debug("Update in progress... waiting for new heartbeat.");
                return;
            }
            updateLockUI("NOT_INSTALLED");
        }
        else if (installedVersion !== requiredVersion) {
            /** 
             * SCENARIO: Version Mismatch.
             * The extension is active in Chrome but the version is old.
             * 
             * Exception: If we're in the middle of an update, don't show lock yet
             */
            if (isUpdatingExtension) {
                console.debug("Update in progress... waiting for version to update.");
                return;
            }

            console.warn(`[Lock] Version Mismatch Detected! (${installedVersion} vs ${requiredVersion})`);

            // Update the version number in the yellow lock box
            const vDisplay = document.getElementById('current-ext-v');
            if (vDisplay) vDisplay.textContent = installedVersion;

            updateLockUI("VERSION_MISMATCH");
        }
        else {
            /** 
             * SCENARIO: Success.
             * Extension is active and version is up to date.
             */
            updateLockUI("INSTALLED");
        }

    } catch (err) {
        console.error("[Bridge] Critical failure polling the relay:", err);
        // On fatal error, assume not installed for safety
        updateLockUI("NOT_INSTALLED");
    }
}

/**
 * Handles the display logic for visual "Security Gates" on the Downloader.
 * 
 * @param {string} state - The determined bridge state.
 */
/**
 * Manages the visual state of the Download Manager Lock overlays.
 * Updated: Removed the Desktop Mode bypass so the lock works in the standalone app.
 */
function updateLockUI(state) {
    const installLock = document.getElementById('idm-extension-lock');
    const versionLock = document.getElementById('idm-version-lock');
    const content = document.getElementById('idm-actual-content');

    if (!installLock || !versionLock || !content) {
        console.error("[UI] Lock elements missing from DOM.");
        return;
    }

    // Default: Hide everything and reset blur
    installLock.style.setProperty('display', 'none', 'important');
    versionLock.style.setProperty('display', 'none', 'important');
    content.style.filter = 'none';
    content.style.opacity = '1';
    content.style.pointerEvents = 'all';

    if (state === "NOT_INSTALLED") {
        console.log("UI: Displaying Installation Lock");
        installLock.style.setProperty('display', 'flex', 'important');
        content.style.filter = 'blur(15px)';
        content.style.opacity = '0.2';
        content.style.pointerEvents = 'none';
    }
    else if (state === "VERSION_MISMATCH") {
        console.log("UI: Displaying Version Mismatch Lock");
        versionLock.style.setProperty('display', 'flex', 'important');
        content.style.filter = 'blur(15px)';
        content.style.opacity = '0.2';
        content.style.pointerEvents = 'none';
    }
}

/**
 * Core Automation: Synchronizes local Extension files then reloads the Browser Worker.
 * Bypasses the need for user manual intervention in Extension settings.
 */
// script.js

// script.js - Simplified & Resilient performExtensionUpdate

/**
 * Fully Automated Extension Update Sequence
 * 
 * 1. Pauses System Monitor to stop background pings (releasing file locks).
 * 2. Waits for a 2-second cooldown period.
 * 3. Calls Python Backend to perform a 'Rename-to-Junk' file swap.
 * 4. Triggers Chrome to hot-reload the extension from the updated folder.
 */
// script.js

/**
 * AUTOMATED UPDATE ENGINE (App Side)
 * 1. Syncs files via Python.
 * 2. Signals Chrome to reload via Relay.
 * 3. Monitors the Relay until the new version reports 'Online'.
 */
async function performExtensionUpdate() {
    if (isUpdatingExtension) return;
    isUpdatingExtension = true;

    const reloadBtn = document.getElementById('ext-reload-btn');
    if (reloadBtn) {
        reloadBtn.disabled = true;
        reloadBtn.innerHTML = '<i class="fas fa-sync-alt fa-spin me-2"></i> SYNCING...';
    }

    try {
        // 1. Overwrite files
        await fetch('/api/extension/sync', { method: 'POST' });
        // 2. Queue the reload command in Python
        await fetch('/api/extension/trigger_reload', { method: 'POST' });

        showToast("Signal sent. Waiting for bridge...", "info");

        // 3. Watchdog Loop: Poll Python until version matches
        let attempts = 0;
        const watchdog = setInterval(async () => {
            attempts++;
            const res = await fetch(`/api/extension/get_last_version?t=${Date.now()}`);
            const data = await res.json();

            console.log(`[Watchdog] Check #${attempts}: Seen v${data.version} | Target v${REQUIRED_EXT_VERSION}`);

            if (data.version === REQUIRED_EXT_VERSION) {
                clearInterval(watchdog);
                isUpdatingExtension = false;
                updateLockUI("INSTALLED");
                showToast("Bridge Updated & Unlocked!", "success");
                if (reloadBtn) {
                    reloadBtn.disabled = false;
                    reloadBtn.innerHTML = '<i class="fas fa-magic me-2"></i> REFRESH BRIDGE';
                }
            }

            if (attempts > 40) {
                clearInterval(watchdog);
                isUpdatingExtension = false;
                showToast("Update timed out. Refresh Chrome manually.", "warning");
                if (reloadBtn) { reloadBtn.disabled = false; reloadBtn.innerHTML = 'REFRESH BRIDGE'; }
            }
        }, 2000);

    } catch (e) {
        isUpdatingExtension = false;
        if (reloadBtn) reloadBtn.disabled = false;
    }
}

/**
 * [11] SYSTEM & PERIPHERAL MONITORING
 * Orchestrates the 5-second polling sequence for hardware telemetry.
 */

function startSystemMonitor() {
    const performPoll = async () => {
        // Stop all polling if we are currently updating files
        if (typeof isUpdatingExtension !== 'undefined' && isUpdatingExtension === true) return;

        try {
            // 1. Fetch System Telemetry (CPU/Ping)
            const sysRes = await fetch('/api/system_stats');
            if (sysRes.ok) {
                const data = await sysRes.json();
                const cpuText = document.getElementById('live-cpu');
                if (cpuText) cpuText.textContent = `${Math.round(data.cpu)}%`;
                const pingText = document.getElementById('live-ping');
                if (pingText) pingText.textContent = `${data.ping} ms`;
            }

            // 2. Fetch Controller Status
            const bridgeRes = await fetch('/api/bridge/status');
            if (bridgeRes.ok) {
                const bData = await bridgeRes.json();
                const batteryText = document.getElementById('batteryText');
                if (batteryText) batteryText.textContent = bData.running ? `${bData.battery}%` : "OFF";
                const toggle = document.getElementById('bridgeToggle');
                if (toggle) toggle.checked = bData.running;
            }

            // 3. PERFORM THE EXTENSION RELAY CHECK
            await checkExtensionStatus();

        } catch (error) {
            console.debug("Monitor: Backend busy.");
        }
    };

    // Delay initial poll to respect grace period and allow extension to initialize
    setTimeout(() => {
        performPoll();
    }, 6000);
    setInterval(performPoll, 5000); // Repeat every 5 seconds
}

/**
 * [12] MODAL & TOOLBOX LOGIC
 */

function openGameProps() {
    if (!currentGame) return;
    document.getElementById('controllerSelect').value = currentGame.controller_type || "None";
    document.getElementById('focusModeToggle').checked = !!currentGame.focus_mode;
    gamePropsModal.show();
}

async function saveGameProps() {
    const data = {
        controller_type: document.getElementById('controllerSelect').value,
        focus_mode: document.getElementById('focusModeToggle').checked
    };
    await writeGameMetadata(data);
    gamePropsModal.hide();
    showToast("Properties saved and cached.", "success");
}

async function openControllerSettings() {
    const call = await fetch('/api/settings');
    const cfg = await call.json();
    document.getElementById('deadzoneRange').value = cfg.controller_deadzone || 0.1;
    document.getElementById('sensRange').value = cfg.controller_sensitivity || 1.0;
    controllerSettingsModal.show();
}

async function saveControllerSettings() {
    const dz = document.getElementById('deadzoneRange').value;
    const sens = document.getElementById('sensRange').value;

    await fetch('/api/bridge/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deadzone: parseFloat(dz), sensitivity: parseFloat(sens) })
    });
    controllerSettingsModal.hide();
    showToast("Controller logic recalibrated.", "success");
}

/**
 * Steam Integration: Fetches real-time status of friends via Python bridge.
 */
async function loadFriends() {
    const listContainer = document.getElementById('friends-list');
    listContainer.innerHTML = `
        <div class="p-5 text-center">
            <div class="spinner-border text-primary mb-2"></div>
            <p class="small text-muted">Scanning Steam API...</p>
        </div>
    `;

    try {
        const response = await fetch('/api/steam/friends');
        const data = await response.json();
        listContainer.innerHTML = '';

        if (data.status === 'error') {
            listContainer.innerHTML = `<div class="p-4 text-center text-danger small">${data.message}</div>`;
            return;
        }

        data.friends.forEach(friend => {
            const statusColor = friend.status === 'In-Game' ? '#90ba3c' : (friend.status === 'Online' ? '#57cbde' : '#666');
            listContainer.insertAdjacentHTML('beforeend', `
                <li class="list-group-item d-flex align-items-center bg-transparent border-secondary gap-3 py-2">
                    <img src="${friend.avatar}" class="rounded-circle" width="38" style="border: 2px solid ${statusColor}">
                    <div class="overflow-hidden">
                        <div class="fw-bold text-white small text-truncate">${friend.name}</div>
                        <div style="font-size:10px; color: ${statusColor}; font-weight:700;">
                            ${friend.status.toUpperCase()} ${friend.game ? '— ' + friend.game : ''}
                        </div>
                    </div>
                </li>
            `);
        });
    } catch (e) {
        listContainer.innerHTML = '<div class="p-4 text-muted text-center small">Friends list unreachable.</div>';
    }
}

/**
 * [13] UTILITY & GLOBAL HELPERS
 */

function showToast(message, type = 'info') {
    const root = document.querySelector('.toast-container');
    if (!root) return;

    const bg = (type === 'success') ? 'bg-success' : (type === 'danger' ? 'bg-danger' : 'bg-primary');
    const tid = `t-${Date.now()}`;

    root.insertAdjacentHTML('beforeend', `
        <div id="${tid}" class="toast align-items-center text-white ${bg} border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body fw-bold">${message}</div>
                <button class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `);

    const node = document.getElementById(tid);
    const toastObj = new bootstrap.Toast(node, { delay: 4000 });
    toastObj.show();
}

async function refreshLibrary(silent = false) {
    if (!silent) showScanOverlay(true);
    await fetch('/api/refresh', { method: 'POST' });
}

function showScanOverlay(visible) {
    const el = document.getElementById('scan-overlay');
    if (el) el.style.display = visible ? 'flex' : 'none';
}

async function browseForGame() {
    const raw = await fetch('/api/browse');
    const data = await raw.json();
    if (data.status === 'success') {
        document.getElementById('gamePathInput').value = data.path;
    }
}

async function browseForDlFolder() {
    const raw = await fetch('/api/browse');
    const data = await raw.json();
    if (data.status === 'success') {
        document.getElementById('dl-save-path').value = data.path;
    }
}

async function saveManualGame() {
    const name = document.getElementById('gameNameInput').value.trim();
    const path = document.getElementById('gamePathInput').value.trim();
    if (name && path) {
        await fetch('/api/add_game', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name, path })
        });
        addGameModal.hide();
        refreshLibrary();
    }
}

async function openSettings() {
    console.log("[Settings] Initializing data retrieval...");

    try {
        // 1. Request current state from Flask API
        const response = await fetch('/api/settings');
        if (!response.ok) throw new Error("Backend server rejected the request.");

        const config = await response.json();

        // 2. Populate API and User Identity Fields
        document.getElementById('apiKeyInput').value = config.steamgriddb_api_key || '';
        document.getElementById('steamApiKeyInput').value = config.steam_api_key || '';
        document.getElementById('steamIdInput').value = config.steam_id || '';

        // 3. Populate UI and Theme Selection
        document.getElementById('themeSelect').value = config.theme || 'default';

        // 4. Populate Chrome Extension Bridge Config
        document.getElementById('extIdInput').value = config.chrome_extension_id || '';
        document.getElementById('extPathInput').value = config.extension_path || '';

        // 5. Populate the new Download Path Field
        const downloadPathInput = document.getElementById('defaultDownloadPathInput');
        if (downloadPathInput) {
            downloadPathInput.value = config.default_download_path || '';
        }

        // 6. Display the Modal using the Bootstrap instance
        if (settingsModal) {
            settingsModal.show();
        } else {
            // Fallback if instance wasn't mapped during init
            const modalEl = document.getElementById('settingsModal');
            bootstrap.Modal.getOrCreateInstance(modalEl).show();
        }

    } catch (error) {
        console.error("[Settings] Critical load failure:", error);
        showToast("Failed to load settings from server.", "danger");
    }
}

/**
 * Aggregates all UI input values and persists them to the backend storage.
 * Synchronizes themes and triggers a library metadata refresh upon success.
 */
async function saveSettings() {
    const saveBtn = document.getElementById('save-settings-btn');

    // 1. Visual Feedback: Disable button and show spinner
    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerHTML = '<i class="fas fa-circle-notch fa-spin me-2"></i> SAVING...';
    }

    // 2. Data Aggregation (Matching API structure)
    const payload = {
        steamgriddb_api_key: document.getElementById('apiKeyInput').value.trim(),
        steam_api_key: document.getElementById('steamApiKeyInput').value.trim(),
        steam_id: document.getElementById('steamIdInput').value.trim(),
        theme: document.getElementById('themeSelect').value,
        chrome_extension_id: document.getElementById('extIdInput').value.trim(),
        extension_path: document.getElementById('extPathInput').value.trim(),
        default_download_path: document.getElementById('defaultDownloadPathInput').value.trim()
    };

    try {
        // 3. Transmit payload to Flask Backend
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (response.ok) {
            // 4. Success Pipeline

            // Apply the new theme to the UI instantly
            applyTheme(payload.theme);

            // Close the window
            if (settingsModal) settingsModal.hide();

            showToast("System configurations updated successfully.", "success");

            // Refresh the library silently to update cover art with new API keys
            refreshLibrary(true);
        } else {
            const errData = await response.json();
            showToast(`Save Failed: ${errData.message || 'Server error'}`, "danger");
        }

    } catch (err) {
        console.error("[Settings] Persistence error:", err);
        showToast("Fatal error: Connection to backend lost.", "danger");
    } finally {
        // 5. Restore Button State
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.innerHTML = "SAVE & REFRESH";
        }
    }
}

async function toggleBridgeHardware(e) {
    const targetState = e.target.checked;
    await fetch('/api/bridge/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enable: targetState })
    });
}

async function openExtensionPath() {
    const req = await fetch('/api/settings');
    const cfg = await req.json();
    if (cfg.extension_path) {
        fetch('/api/open_folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: cfg.extension_path })
        });
    } else {
        showToast("Path configuration missing.", "warning");
        settingsModal.show();
    }
}

async function checkForAppUpdates() {
    try {
        const req = await fetch('/api/check_for_updates');
        const data = await req.json();
        const btn = document.getElementById('update-btn');

        if (data.update_available) {
            if (btn) {
                btn.style.display = 'block';
                btn.onclick = () => {
                    btn.disabled = true;
                    btn.innerHTML = 'UPDATING...';
                    fetch('/api/perform_update', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ url: data.url })
                    });
                };
            }
            showToast(`Update Available: v${data.version}`, "info");
        }
    } catch (e) {
        console.debug("[Update] Remote check failed.");
    }
}

async function checkActiveGame() {
    try {
        const res = await fetch('/api/game_status');
        const data = await res.json();
        if (data && data.name) activeGameData = data;
    } catch (err) {
        console.debug("[Startup] Process telemetry sync skipped.");
    }
}

async function restoreDownloads() {
    try {
        const res = await fetch('/api/downloads/list');
        const tasks = await res.json();
        tasks.forEach(task => updateDownloadUI(task));
    } catch (e) {
        console.debug("[Startup] Download list sync skipped.");
    }
}

/**
 * Toggles the automated controller bridge for the currently selected game.
 * Syncs the state to the database and refreshes the UI button style.
 */
async function toggleAutoBridge() {
    if (!currentGame) return;

    // Toggle the boolean state
    const newState = !currentGame.auto_bridge;

    // Update both backend database and local memory
    await writeGameMetadata({ auto_bridge: newState });

    // Refresh the details panel to show the active/inactive state
    showGameDetails(currentGame);

    console.log(`[Bridge] Auto-Bridge for ${currentGame.name} set to: ${newState}`);
}

/**
 * Triggers the native folder picker and updates the settings input.
 */
async function browseForDownloadFolder() {
    console.log("[Settings] Opening folder picker...");

    try {
        const response = await fetch('/api/browse_folder');

        // --- FIX: Check for server error (500) before parsing JSON ---
        if (!response.ok) {
            const errorText = await response.text();
            console.error("Server Error:", errorText);
            showToast("Server failed to open folder picker. Check Python console.", "danger");
            return;
        }

        const data = await response.json();

        if (data.status === 'success') {
            const input = document.getElementById('defaultDownloadPathInput');
            if (input) {
                input.value = data.path;
                showToast("Folder selected successfully.", "success");
            }
        }
    } catch (err) {
        console.error("Fatal error opening folder browser:", err);
        showToast("System error: Could not reach the backend.", "danger");
    }
}

/**
 * Loads backend configuration and applies UI defaults.
 * Fixed: Added response validation to prevent HTML-as-JSON crashes.
 */
async function loadConfiguration() {
    try {
        const response = await fetch('/api/settings');

        // --- FIX: Check if the response is actually okay before parsing ---
        if (!response.ok) {
            console.error(`[Config] Server returned ${response.status}. Check your app.py routes.`);
            applyTheme("default"); // Fallback
            return;
        }

        const config = await response.json();

        if (config && config.theme) {
            applyTheme(config.theme);
        }

        console.log("[Config] User settings loaded.");
    } catch (err) {
        console.warn("[Config] Error parsing settings, using defaults:", err);
        applyTheme("default");
    }
}

/**
 * Permanently removes the currently selected game from the library database.
 */
/**
 * Deletes the selected game and immediately removes it from the UI.
 */
/**
 * Deletes the selected game and immediately removes it from the UI.
 */
async function deleteCurrentGame() {
    if (!currentGame) return;

    const gameName = currentGame.name;
    const gameSource = currentGame.source;

    // 1. Double check with user
    if (!confirm(`Permanently remove "${gameName}" from your library?`)) return;

    try {
        // 2. Tell the backend to delete from SQL
        const response = await fetch('/api/delete_game', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: gameName, source: gameSource })
        });

        const result = await response.json();

        if (result.status === 'success') {
            showToast(`"${gameName}" has been removed.`, "info");

            // 3. CRITICAL: Clear the current selection
            currentGame = null;

            // 4. CRITICAL: Force the UI to fetch the new list and re-draw the grid
            // This ensures the deleted game vanishes from the screen immediately.
            await fetchGamesAndRender();

            // 5. Reset the sidebar to empty state
            document.getElementById('game-title').textContent = "Select a Game";
            document.getElementById('hero-image-container').innerHTML = `
                <div class="h-100 w-100 d-flex align-items-center justify-content-center bg-dark">
                    <i class="fas fa-gamepad fa-4x opacity-10"></i>
                </div>
            `;
            document.getElementById('delete-game-btn').style.display = 'none';
            document.body.style.setProperty('--active-bg', 'none');

        } else {
            showToast("Error: " + result.message, "danger");
        }
    } catch (err) {
        console.error("Delete sequence failed:", err);
        showToast("Backend connection error.", "danger");
    }
}

/**
 * CRITICAL UTILITY: Wipes the local SQLite database and triggers a fresh scan.
 * Use this to fix ID mismatches or corrupted library data.
 */
async function wipeAndRescan() {
    // 1. Safety Confirmation
    const confirmed = confirm("WARNING: This will delete all custom artwork, manual additions, and playtime data. Are you sure you want to reset the database?");

    if (!confirmed) return;

    try {
        // 2. Tell Python to clear the SQL tables
        const response = await fetch('/api/danger/wipe_db', { method: 'POST' });
        const result = await response.json();

        if (result.status === 'success') {
            showToast("Database wiped. Re-scanning system...", "info");

            // 3. Clear the local memory
            allGames = [];
            currentGame = null;

            // 4. Trigger a full fresh scan
            await refreshLibrary(false);

            // 5. Close settings
            if (settingsModal) settingsModal.hide();
        } else {
            showToast("Error wiping database: " + result.message, "danger");
        }
    } catch (err) {
        console.error("Wipe failed:", err);
        showToast("Backend connection failed.", "danger");
    }
}

/**
 * Pings the backend to download a new extension version from the cloud.
 */
async function checkRemoteExtensionUpdate() {
    const btn = document.getElementById('ext-remote-update-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-cloud-download-alt fa-spin me-2"></i> CHECKING CLOUD...';
    }

    try {
        const res = await fetch('/api/extension/update_remote', { method: 'POST' });
        const data = await res.json();

        if (data.status === "success") {
            showToast("Extension updated from cloud!", "success");
            // Trigger the browser reload
            performExtensionUpdate();
        } else {
            showToast("Extension is up to date.", "info");
        }
    } catch (e) {
        showToast("Cloud update failed.", "danger");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="fas fa-cloud-download-alt me-2"></i> UPDATE EXTENSION';
        }
    }
}

/**
 * SILENT BACKGROUND CHECK: Pings GitHub for the latest extension version.
 * This runs once on startup, NOT when clicking settings.
 */
async function checkForExtensionCloudUpdate() {
    const updateBtn = document.getElementById('ext-remote-update-btn');
    if (!updateBtn) return;

    try {
        // 1. Get current local version from backend
        const configRes = await fetch('/api/settings');
        const config = await configRes.json();

        // Default to your hardcoded version if settings are empty
        const currentLocalVersion = config.extension_internal_version || REQUIRED_EXT_VERSION;

        // 2. Ping GitHub for version metadata
        const githubUrl = "https://raw.githubusercontent.com/Ziadnahouli/GameHub/main/extension_version.json";
        const res = await fetch(githubUrl);

        if (!res.ok) return; // Exit silently if GitHub is down
        const remoteData = await res.json();

        // 3. Comparison
        if (remoteData.version !== currentLocalVersion) {
            console.log(`[Updater] New Extension found: v${remoteData.version}`);
            // Show the button permanently for this session
            updateBtn.style.setProperty('display', 'block', 'important');
        } else {
            // Ensure it stays hidden if up to date
            updateBtn.style.display = 'none';
        }
    } catch (e) {
        // Fail silently so the user never sees an error
        console.debug("Extension cloud check skipped.");
    }
}




/**
 * Detects if the UI is running inside the Standalone Desktop App (PyWebView)
 * or inside a standard web browser (Chrome/Edge).
 */
function isDesktopMode() {
    // In PyWebView, the 'chrome' object and 'runtime' are not available.
    return typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage;
}

// background.js



// Polling for extension status (Every 5 seconds)
setInterval(checkExtensionStatus, 5000);





// Load default download settings from config
async function loadDefaultDownloadSettings() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();

        document.getElementById('defaultSplitsInput').value = config.default_splits || 16;
        document.getElementById('defaultConnectionsInput').value = config.default_connections || 16;
        document.getElementById('defaultSpeedLimitInput').value = config.default_speed_limit || 0;
    } catch (err) {
        console.error("Failed to load default download settings:", err);
    }
}

// Save default download settings to config
async function saveDefaultDownloadSettings() {
    const splits = parseInt(document.getElementById('defaultSplitsInput').value) || 16;
    const connections = parseInt(document.getElementById('defaultConnectionsInput').value) || 16;
    const speedLimit = parseInt(document.getElementById('defaultSpeedLimitInput').value) || 0;

    try {
        await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                default_splits: splits,
                default_connections: connections,
                default_speed_limit: speedLimit
            })
        });
        showToast("Default download settings saved!", "success");
    } catch (err) {
        showToast("Failed to save settings.", "danger");
    }
}

// Call this when settings modal is opened
document.getElementById('settings-btn')?.addEventListener('click', loadDefaultDownloadSettings);

// Save settings when modal is closed (you can also add a dedicated save button)
document.getElementById('settingsModal')?.addEventListener('hidden.bs.modal', saveDefaultDownloadSettings);

// Final Engine Validation
console.log("%c [SYSTEM] MASTER UI CONTROLLER FULLY DEPLOYED ", "color: #2ea043; font-weight: 800;");

/**
 * ============================================================================
 * [14] AI GAME OPTIMIZER MODULE
 * ============================================================================
 */

let optimizerBenchmarkData = null;

function renderOptimizerPanel(game) {
    const container = document.getElementById('optimizer-ui-container');
    if (!container) return;

    // Only show for main library sources
    container.style.display = 'block';

    // 1. Determine State
    const isRunning = activeGameData && activeGameData.name === game.name;
    const isBenchmarking = optimizerBenchmarkData && optimizerBenchmarkData.state === 'RUNNING' && optimizerBenchmarkData.unique_id.includes(game.name);
    const isDone = optimizerBenchmarkData && optimizerBenchmarkData.state === 'DONE' && optimizerBenchmarkData.unique_id.includes(game.name);

    let html = `
        <div class="optimizer-header">
            <h5 class="m-0"><i class="fas fa-microchip me-2" style="color: var(--accent);"></i> AI Game Optimizer</h5>
            <div class="d-flex gap-2">
                 ${(isDone) ?
            `<button class="btn btn-sm btn-outline-light" onclick="restoreSnapshot()"><i class="fas fa-undo me-1"></i> Restore</button>` : ''}
            </div>
        </div>
    `;

    if (!isRunning) {
        html += `
            <div class="text-center p-4 text-muted">
                <i class="fas fa-gamepad fa-2x mb-3 opacity-50"></i>
                <p>Launch <strong>${game.name}</strong> to start the optimizer.</p>
            </div>
        `;
    } else if (isBenchmarking) {
        // RUNNING STATE
        const d = optimizerBenchmarkData;
        const progress = d.seconds_total > 0 ? (d.samples_collected / d.seconds_total) * 100 : 0;

        html += `
            <div class="benchmark-progress-container">
                <div class="d-flex justify-content-between mb-1">
                    <span class="small fw-bold text-white">BENCHMARKING SYSTEM...</span>
                    <span class="small text-muted">${d.seconds_left}s remaining</span>
                </div>
                <div class="progress" style="height: 6px; background: rgba(0,0,0,0.5);">
                    <div class="progress-bar progress-bar-striped progress-bar-animated bg-info" style="width: ${progress}%"></div>
                </div>
                
                <div class="live-stats-grid">
                    <div class="live-stat-box">
                        <span class="live-stat-label">FPS</span>
                        <span class="live-stat-value" style="color: #2ecc71;">${d.fps_current ? Math.round(d.fps_current) : '--'}</span>
                    </div>
                    <div class="live-stat-box">
                        <span class="live-stat-label">CPU</span>
                        <span class="live-stat-value">${Math.round(d.cpu_current || 0)}%</span>
                    </div>
                    <div class="live-stat-box">
                        <span class="live-stat-label">RAM</span>
                        <span class="live-stat-value">${Math.round(d.ram_current || 0)}%</span>
                    </div>
                    <div class="live-stat-box">
                        <span class="live-stat-label">SAMPLES</span>
                        <span class="live-stat-value">${d.samples_collected}</span>
                    </div>
                </div>
                
                <div class="text-center mt-3">
                    <button class="btn btn-sm btn-danger" onclick="stopBenchmark()">CANCEL</button>
                </div>
            </div>
        `;
    } else if (isDone) {
        // RESULTS STATE
        const d = optimizerBenchmarkData;
        html += `
            <div class="benchmark-progress-container">
                <div class="text-center mb-3">
                    <h6 class="text-white fw-bold">ANALYSIS COMPLETE</h6>
                    <div class="bottleneck-display">
                        BOTTLENECK: ${d.bottleneck.toUpperCase()}
                    </div>
                </div>
                
                <div class="live-stats-grid mb-3">
                    <div class="live-stat-box">
                        <span class="live-stat-label">AVG FPS</span>
                        <span class="live-stat-value">${Math.round(d.fps_avg || 0)}</span>
                    </div>
                    <div class="live-stat-box">
                        <span class="live-stat-label">1% LOW</span>
                        <span class="live-stat-value text-danger fs-6">${Math.round(d.fps_1_low || 0)}</span>
                    </div>
                     <div class="live-stat-box">
                        <span class="live-stat-label">CPU AVG</span>
                        <span class="live-stat-value">${Math.round(d.cpu_avg || 0)}%</span>
                    </div>
                    <div class="live-stat-box">
                        <span class="live-stat-label">RAM AVG</span>
                        <span class="live-stat-value">${Math.round(d.ram_avg || 0)}%</span>
                    </div>
                </div>

                <h6 class="text-muted small fw-bold mb-2 text-uppercase">Recommended Actions</h6>
                <div class="rec-list">
                    ${d.recommendations.map(rec => `
                        <div class="rec-card">
                            <div class="rec-info">
                                <h6>${rec.title} ${rec.requires_admin ? '<span class="admin-badge">ADMIN</span>' : ''}</h6>
                                <p>${rec.reason}</p>
                            </div>
                            <div class="form-check form-switch">
                                <input class="form-check-input rec-toggle" type="checkbox" data-apply='${JSON.stringify(rec.apply_payload)}' checked>
                            </div>
                        </div>
                    `).join('')}
                    ${d.recommendations.length === 0 ? '<div class="text-muted small text-center">No optimizations needed!</div>' : ''}
                </div>
                
                <div class="d-flex gap-2 mt-3">
                    <button class="btn btn-primary w-100 fw-bold" onclick="applyOptimizations()">
                        <i class="fas fa-magic me-2"></i> APPLY SELECTED
                    </button>
                    ${activeGameData ? '<button class="btn btn-outline-light" onclick="startBenchmark()">RE-RUN</button>' : ''}
                </div>
            </div>
        `;
    } else {
        // IDLE STATE (Game Running)
        html += `
            <div class="d-flex align-items-center justify-content-between p-3" style="background: rgba(255,255,255,0.05); border-radius: 8px;">
                <div>
                    <h6 class="text-white mb-1">Performance Benchmark</h6>
                    <p class="text-muted small m-0">Analyze bottlenecks for 60s</p>
                </div>
                <div class="d-flex gap-2">
                    <button id="overlay-toggle-btn" class="btn btn-sm btn-outline-light" onclick="toggleOverlay()">
                        <i class="fas fa-eye me-2"></i> Show FPS
                    </button>
                    <button class="btn btn-outline-info fw-bold" onclick="startBenchmark()">
                        START
                    </button>
                </div>
            </div>
        `;
    }

    container.innerHTML = html;
}

// --- CONTROLS ---

async function startBenchmark(game) {
    const target = game || currentGame;
    if (!activeGameData || !target) return;

    // Use the backend-provided PID from activeGameData
    const pid = activeGameData.pid;
    const unique_id = `${target.source}|${target.name}`;

    optimizerBenchmarkData = { state: 'STARTING', unique_id: unique_id };
    renderOptimizerPanel(target);

    try {
        const res = await fetch('/api/optimizer/benchmark/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                unique_id: unique_id,
                pid: pid,
                exe_path: target.install_path,
                target_mode: 'Balanced'
            })
        });
        const data = await res.json();
        if (data.status === 'error') {
            showToast(data.message, "danger");
            optimizerBenchmarkData = null;
            renderOptimizerPanel(target);
        }
    } catch (e) {
        showToast("Failed to start bench: " + e.message, "danger");
        optimizerBenchmarkData = null;
        renderOptimizerPanel(target);
    }
}

async function stopBenchmark(game) {
    const target = game || currentGame;
    if (!target) return;
    const unique_id = `${target.source}|${target.name}`;
    await fetch('/api/optimizer/benchmark/stop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ unique_id: unique_id })
    });
}

async function applyOptimizations(game) {
    const target = game || currentGame;
    if (!target) return;
    const unique_id = `${target.source}|${target.name}`;

    // Gather toggled items
    const toggles = document.querySelectorAll('.rec-toggle:checked');
    const actions = Array.from(toggles).map(t => JSON.parse(t.dataset.apply));

    if (actions.length === 0) {
        showToast("No actions selected", "warning");
        return;
    }

    // Ensure user knows about closed apps
    const closingApps = actions.some(a => a.type === 'close_apps');
    if (closingApps && !confirm("Warning: This will close background apps. They cannot be automatically re-opened. Continue?")) {
        return;
    }

    try {
        const res = await fetch('/api/optimizer/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ unique_id: unique_id, actions: actions })
        });
        const data = await res.json();

        if (data.status === 'success') {
            showToast("Optimizations Applied!", "success");
            const container = document.getElementById('optimizer-ui-container');
            container.innerHTML = `
                <div class="optimizer-header">
                    <h5><i class="fas fa-check text-success me-2"></i> System Optimized</h5>
                </div>
                <div class="alert alert-success bg-opacity-10 bg-success text-white border-0">
                    Changes applied successfully.
                </div>
                <button class="btn btn-outline-light w-100" onclick="startBenchmark()">Verify Improvements</button>
             `;
            optimizerBenchmarkData = null;
        } else {
            showToast("Error: " + data.message, "danger");
        }
    } catch (e) {
        showToast("Failed to apply optimizations", "danger");
    }
}

async function restoreSnapshot(game) {
    const target = game || currentGame;
    if (!target) return;
    const unique_id = `${target.source}|${target.name}`;
    if (!confirm("Revert system settings? Closed apps will not be re-opened.")) return;

    try {
        const res = await fetch('/api/optimizer/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ unique_id: unique_id })
        });
        const data = await res.json();
        if (data.status === 'success') {
            showToast("Settings Restored.", "success");
            optimizerBenchmarkData = null;
            renderOptimizerPanel(target);
        } else {
            showToast("Restore failed: " + data.message, "danger");
        }
    } catch (e) {
        showToast("Restore request failed", "danger");
    }
}

let isOverlayOn = false;
async function toggleOverlay() {
    isOverlayOn = !isOverlayOn;
    const btn = document.getElementById('overlay-toggle-btn');
    if (btn) {
        btn.innerHTML = isOverlayOn ? '<i class="fas fa-eye-slash me-2"></i> Hide FPS' : '<i class="fas fa-eye me-2"></i> Show FPS';
        btn.classList.toggle('btn-outline-warning', isOverlayOn);
        btn.classList.toggle('btn-outline-light', !isOverlayOn);
    }

    await fetch('/api/optimizer/overlay/toggle', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: isOverlayOn })
    });
}