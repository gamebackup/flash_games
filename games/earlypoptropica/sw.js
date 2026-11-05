/*
    Local account backend for the Early Poptropica mirror.

    The client posts to a set of PHP endpoints (login.php, part-register.php, ...).
    A static host cannot answer those - it returns 405/501 - so this service worker
    intercepts them for this folder only and answers from IndexedDB in this browser.
    Nothing leaves the machine; accounts are per-browser, per-origin.

    Scope note: a service worker sees only requests under its own scope, so the
    client's relative calls (login.php, part-register.php, photos/...) are handled,
    while root-absolute strays like /getPrefix.php never reach it.
*/
"use strict";

const DB_NAME = "pop-accounts";
const STORE = "accounts";

/* ---------- IndexedDB helpers (localStorage is unavailable in a SW) ---------- */

function openDB() {
    return new Promise(function (resolve, reject) {
        const req = indexedDB.open(DB_NAME, 1);
        req.onupgradeneeded = function () {
            if (!req.result.objectStoreNames.contains(STORE))
                req.result.createObjectStore(STORE, { keyPath: "username" });
        };
        req.onsuccess = function () { resolve(req.result); };
        req.onerror = function () { reject(req.error); };
    });
}

function withStore(mode, fn) {
    return openDB().then(function (db) {
        return new Promise(function (resolve, reject) {
            const tx = db.transaction(STORE, mode);
            const out = fn(tx.objectStore(STORE));
            tx.oncomplete = function () { resolve(out && out.result); };
            tx.onerror = function () { reject(tx.error); };
        });
    });
}

const getAccount = function (username) { return withStore("readonly", function (s) { return s.get(username); }); };
const putAccount = function (rec) { return withStore("readwrite", function (s) { return s.put(rec); }); };
const allAccounts = function () { return withStore("readonly", function (s) { return s.getAll(); }); };

/* ------------------------------- HTTP helpers ------------------------------- */

function formResponse(obj) {
    return new Response(new URLSearchParams(obj).toString(), {
        status: 200,
        headers: { "Content-Type": "text/plain; charset=utf-8" }
    });
}

function jsonResponse(obj) {
    return new Response(JSON.stringify(obj), {
        status: 200,
        headers: { "Content-Type": "application/json" }
    });
}

async function requestParams(request) {
    const url = new URL(request.url);
    const out = {};
    url.searchParams.forEach(function (v, k) { out[k] = v; });

    if (request.method === "POST") {
        try {
            const text = await request.clone().text();
            new URLSearchParams(text).forEach(function (v, k) { out[k] = v; });
        } catch (err) { /* body not readable as text - fine */ }
    }

    return out;
}

function newDbId() {
    return String(Date.now()) + String(Math.floor(Math.random() * 1000));
}

/* -------------------------------- endpoints -------------------------------- */

const ENDPOINTS = {
    /* Returns JSON: gameplay.swf parses it with jsonLib and reads the character. */
    async "login.php"(p) {
        const username = (p.username || p.sender || p.name || p.user || "").trim();
        const pass = p.pass_hash || p.passhash || p.hash || p.password || "";

        if (!username)
            return formResponse({ error: "noname" });

        const rec = await getAccount(username.toLowerCase());
        if (!rec)
            return formResponse({ error: "noname" });

        if (rec.pass_hash && pass && rec.pass_hash !== pass)
            return formResponse({ error: "wrongpass" });

        const payload = {
            ok: 1,
            success: 1,
            dbid: rec.dbid,
            username: rec.username,
            gender: rec.gender || "M",
            age: rec.age || "",
            look: rec.look || "",
            credits: rec.credits || "75",
            mem_status: "0",
            memstatus: "0",
            mem_date: "0",
            parent_email: "",
            has_parent_email: "0",
            Registred: "1",
            registered: "1",
            island: rec.island || p.island || "Early",
            room: rec.room || p.room || "City2",
            last_room: rec.room || p.room || "City2",
            // gameplay.swf's doLogin reads these exact spellings off the parsed
            // json ("lastIsland", "lastRoom", "password") when it restores the
            // character, so send all the variants rather than only snake_case.
            lastIsland: rec.island || p.island || "Early",
            lastRoom: rec.room || p.room || "City2",
            password: pass || rec.pass_hash || "",
            returning: "1"
        };

        // Two different callers share this endpoint and parse it differently:
        // sceneHome.swf loads the reply into a LoadVars (it looks for wrongpass /
        // noname), while gameplay.swf parses JSON with jsonLib. They are told apart
        // by parameter name - the login screen sends "sender", the in-game relogin
        // sends "username".
        return p.sender ? formResponse(payload) : jsonResponse(payload);
    },

    /* Character data for the account being created. */
    async "part-register.php"(p) {
        const username = (p.username || p.sender || p.name || "").trim().toLowerCase() || "player";
        const rec = (await getAccount(username)) || {
            username: username,
            dbid: newDbId(),
            credits: "75",
            created: Date.now()
        };

        rec.fname = p.fname || p.firstName || rec.fname || "";
        rec.lname = p.lname || p.lastName || rec.lname || "";
        rec.look = p.look || p.char || p.avatar || rec.look || "";
        rec.gender = p.gender || rec.gender || "M";
        rec.age = p.age || rec.age || "";
        rec.pass_hash = p.pass_hash || rec.pass_hash || "";

        await putAccount(rec);
        return formResponse({ success: 1, ok: 1, username: username, dbid: rec.dbid });
    },

    /* Account creation from the "Save your game" dialog. */
    async "register.php"(p) {
        const username = (p.username || p.name || "").trim();
        const pass = p.pass_hash || p.passhash || p.password || "";

        if (!username)
            return formResponse({ error: "noname" });

        const key = username.toLowerCase();
        if (await getAccount(key))
            return formResponse({ error: "namenottaken" });

        const rec = {
            username: key,
            pass_hash: pass,
            dbid: newDbId(),
            credits: "75",
            gender: p.gender || "M",
            look: p.look || p.char || "",
            created: Date.now()
        };

        await putAccount(rec);
        return formResponse({ success: 1, ok: 1, dbid: rec.dbid, username: key });
    },

    /* Restores accounts from an exported file, without clobbering a live account. */
    async "__pop_import_accounts.php"(p, request) {
        let payload;

        try {
            payload = JSON.parse(await request.text());
        } catch (err) {
            return jsonResponse({ ok: 0, error: "badjson" });
        }

        const list = Array.isArray(payload) ? payload : (payload && payload.accounts) || [];
        let imported = 0, skipped = 0;

        for (const rec of list) {
            if (!rec || typeof rec.username !== "string" || !rec.username.trim()) {
                skipped++;
                continue;
            }

            const username = rec.username.trim().toLowerCase();

            if (await getAccount(username)) {   // already here: leave it alone
                skipped++;
                continue;
            }

            await putAccount({
                username: username,
                pass_hash: rec.pass_hash || "",
                dbid: rec.dbid || newDbId(),
                credits: rec.credits || "75",
                gender: rec.gender || "M",
                age: rec.age || "",
                look: rec.look || "",
                created: rec.created || Date.now()
            });
            imported++;
        }

        return jsonResponse({ ok: 1, imported: imported, skipped: skipped });
    },

    /* Removes one account from this browser (used by the on-page manager). */
    async "__pop_delete_account.php"(p) {
        const username = (p.username || "").trim().toLowerCase();
        if (!username)
            return jsonResponse({ ok: 0, error: "noname" });

        await withStore("readwrite", function (s) { return s.delete(username); });
        return jsonResponse({ ok: 1, deleted: username });
    },

    /* Remembers which avatar belongs to an account, so switching accounts brings
       back the character that was being played. */
    async "__pop_set_char.php"(p, request) {
        let payload;

        try {
            payload = JSON.parse(await request.text());
        } catch (err) {
            return jsonResponse({ ok: 0, error: "badjson" });
        }

        const username = String(payload.username || "").trim().toLowerCase();
        if (!username)
            return jsonResponse({ ok: 0, error: "noname" });

        const rec = await getAccount(username);
        if (!rec)
            return jsonResponse({ ok: 0, error: "noname" });

        rec.char = payload.char && typeof payload.char === "object" ? payload.char : null;
        await putAccount(rec);

        return jsonResponse({ ok: 1, username: username });
    },

    /* Debug helper: lets the page list what is stored in this browser. */
    async "__pop_accounts.php"() {
        return jsonResponse({ accounts: await allAccounts() });
    }
};

// The "Save your game" dialog (popups/register.swf) posts to reguser.php, which used
// to be root-absolute ("/reguser.php") and so fell outside this worker's scope. The
// SWF's string was byte-patched to "../"-less "./reguser.php" - same length - and it
// takes the same fields as register.php.
ENDPOINTS["reguser.php"] = ENDPOINTS["register.php"];

/* --------------------------------- plumbing -------------------------------- */

self.addEventListener("install", function () { self.skipWaiting(); });
self.addEventListener("activate", function (event) { event.waitUntil(self.clients.claim()); });

self.addEventListener("fetch", function (event) {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;

    const name = url.pathname.split("/").pop();
    if (!name || !name.endsWith(".php")) return;      // only the client's .php calls

    const handler = ENDPOINTS[name];
    event.respondWith((async function () {
        try {
            const params = await requestParams(event.request);
            if (handler) return await handler(params, event.request);

            // Anything else the client asks for: succeed quietly instead of 501/404.
            return formResponse({ ok: 1, success: 1 });
        } catch (err) {
            return formResponse({ error: "dberror", ok: 0 });
        }
    })());
});
