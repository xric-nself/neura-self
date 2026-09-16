# This file is part of NeuraSelf-UwU.
# Copyright (c) 2025-Present Routo
#
# NeuraSelf-UwU is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with NeuraSelf-UwU. If not, see <https://www.gnu.org/licenses/>.


"""
Author: Routo
NeuraSelf-UwU - https://github.com/routo-loop/neura-self

BrowserUse captcha service for NeuraSelf.
Runs the full OwO hCaptcha flow inside a remote Chromium browser
via BrowserUse Cloud API.
"""

import os
import asyncio
import random
import aiohttp
from datetime import datetime, timezone


class BrowserUseService:

    # Sessions older than this are considered orphaned and safe to stop.
    CLEANUP_AGE_MINUTES = 10

    # Wide pool of ISO 3166-1 alpha-2 country codes.
    # Invalid ones will fail on _create_browser and get skipped.
    ALL_COUNTRIES = [
        "us", "gb", "de", "ca", "fr", "jp", "au", "fi", "it", "in",
        "nl", "es", "se", "no", "dk", "be", "at", "ch", "ie", "pt",
        "pl", "cz", "gr", "ro", "hu", "bg", "hr", "sk", "si", "lt",
        "lv", "ee", "br", "mx", "ar", "cl", "co", "pe", "za", "ng",
        "eg", "il", "ae", "sa", "tr", "kr", "sg", "my", "th", "id",
        "ph", "vn", "nz", "hk", "tw",
    ]

    def __init__(self, bot, api_key, site_key):
        self.bot = bot
        self.api_key = api_key or os.getenv("BROWSER_USE_API_KEY", "")
        self.site_key = site_key
        self.base_url = "https://api.browser-use.com/api/v4"
        self.oauth_url = "https://discord.com/api/v9/oauth2/authorize?client_id=408785106942164992&response_type=code&redirect_uri=https://owobot.com/api/auth/discord/redirect&scope=identify guilds"
        self.captcha_url = "https://owobot.com/captcha"

        # Tracking state — survives across captchas
        self.recent_good = []   # Countries that recently succeeded
        self.recent_fail = []   # Countries that recently failed OAuth

    async def get_balance(self):
        return 999

    async def solve_hcaptcha(self, retries=3):
        return None

    def _still_solving(self):
        return getattr(self.bot, '_solving_captcha', True)

    def _build_country_order(self):
        """Return a shuffled list of countries, biased toward recent wins."""
        pool = list(self.ALL_COUNTRIES)
        random.shuffle(pool)

        # Move recently successful countries to front
        good_ordered = []
        for c in reversed(self.recent_good):
            if c in pool:
                pool.remove(c)
                good_ordered.append(c)

        # Push recently failed countries to back
        for c in self.recent_fail:
            if c in pool:
                pool.remove(c)
                pool.append(c)

        return good_ordered + pool

    def _note_success(self, country):
        if country in self.recent_fail:
            self.recent_fail.remove(country)
        if country not in self.recent_good:
            self.recent_good.append(country)
        self.recent_good = self.recent_good[-5:]

    def _note_fail(self, country):
        if country in self.recent_good:
            self.recent_good.remove(country)
        if country not in self.recent_fail:
            self.recent_fail.append(country)
        self.recent_fail = self.recent_fail[-10:]

    async def _cleanup_old_sessions(self):
        if not self.api_key:
            return

        try:
            headers = {"X-Browser-Use-API-Key": self.api_key}
            url = self.base_url + "/browsers?status=active&pageSize=100"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()

            items = data.get("items", []) or []
            if not items:
                return

            now = datetime.now(timezone.utc)
            cleaned = 0

            for item in items:
                session_id = item.get("id")
                started_at_str = item.get("startedAt")
                if not session_id or not started_at_str:
                    continue

                try:
                    started_at = datetime.fromisoformat(started_at_str)
                    if started_at.tzinfo is None:
                        started_at = started_at.replace(tzinfo=timezone.utc)
                except Exception:
                    continue

                age_minutes = (now - started_at).total_seconds() / 60.0
                if age_minutes < self.CLEANUP_AGE_MINUTES:
                    continue

                try:
                    await self._stop_browser(session_id)
                    cleaned += 1
                except Exception:
                    pass

            if cleaned > 0:
                self.bot.log("SYS", "BrowserUse: Cleaned up " + str(cleaned) + " orphaned session(s).")

        except Exception as e:
            self.bot.log("WARN", "BrowserUse: Cleanup sweep warning: " + str(e))

    async def _create_browser(self, proxy_country="us"):
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.base_url + "/browsers",
                headers={
                    "X-Browser-Use-API-Key": self.api_key,
                    "Content-Type": "application/json",
                },
                json={"proxyCountryCode": proxy_country},
            ) as resp:
                if resp.status not in (200, 201):
                    err = await resp.text()
                    self.bot.log("WARN", "BrowserUse: Failed to create browser with '" + proxy_country + "': " + str(resp.status) + " - " + err[:200])
                    return None, None
                data = await resp.json()
                return data.get("id"), data.get("cdpUrl")

    async def _stop_browser(self, session_id):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    self.base_url + "/browsers/" + session_id,
                    headers={
                        "X-Browser-Use-API-Key": self.api_key,
                        "Content-Type": "application/json",
                    },
                    json={"action": "stop"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status not in (200, 204):
                        err = await resp.text()
                        self.bot.log("WARN", "BrowserUse: Stop session " + session_id[:8] + " failed (" + str(resp.status) + "): " + err[:200])
        except Exception as e:
            self.bot.log("WARN", "BrowserUse: Stop session exception: " + str(e))

    async def solve_full_flow(self, discord_token, retries=3):
        if not self.api_key:
            self.bot.log("ERROR", "BrowserUse: BROWSER_USE_API_KEY is not set.")
            return False

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.bot.log("ERROR", "BrowserUse: playwright not installed.")
            return False

        await self._cleanup_old_sessions()

        country_order = self._build_country_order()

        for attempt in range(retries):
            if not self._still_solving():
                self.bot.log("SUCCESS", "BrowserUse: DM verification already received — stopping retries.")
                return True

            country = country_order[attempt % len(country_order)]
            self.bot.log("SYS", "BrowserUse: Attempt " + str(attempt + 1) + "/" + str(retries) + " (proxy: " + country + ")")

            browser = None
            session_id = None
            solved_via_dm = False
            oauth_failed = False
            try:
                session_id, cdp_url = await self._create_browser(proxy_country=country)
                if not cdp_url:
                    # Country didn't work — try next attempt
                    await asyncio.sleep(2)
                    continue

                self.bot.log("SYS", "BrowserUse: Browser created, connecting via CDP...")

                async with async_playwright() as p:
                    browser = await p.chromium.connect_over_cdp(cdp_url, timeout=60000)
                    context = browser.contexts[0]
                    page = context.pages[0] if context.pages else await context.new_page()

                    async def add_auth(route):
                        headers = route.request.headers.copy()
                        headers["authorization"] = discord_token
                        await route.continue_(headers=headers)

                    await page.route("**/discord.com/api/**", add_auth)
                    await page.route("**/owobot.com/api/**", add_auth)

                    self.bot.log("SYS", "BrowserUse: Loading owobot.com...")
                    try:
                        await page.goto("https://owobot.com", wait_until="domcontentloaded", timeout=45000)
                    except Exception as e:
                        self.bot.log("WARN", "BrowserUse: owobot.com load warning: " + str(e))

                    await asyncio.sleep(2)

                    self.bot.log("SYS", "BrowserUse: Authorizing Discord OAuth...")
                    try:
                        oauth_result = await page.evaluate("""async (token) => {
                            try {
                                const res = await fetch("https://discord.com/api/v9/oauth2/authorize?client_id=408785106942164992&response_type=code&redirect_uri=https://owobot.com/api/auth/discord/redirect&scope=identify%20guilds", {
                                    method: "POST",
                                    headers: {
                                        "Authorization": token,
                                        "Content-Type": "application/json"
                                    },
                                    body: JSON.stringify({
                                        authorize: true,
                                        permissions: "0",
                                        integration_type: 0,
                                        location_context: {guild_id: "10000", channel_id: "10000", channel_type: 10000}
                                    })
                                });
                                const data = await res.json();
                                return {status: res.status, data: data};
                            } catch(e) {
                                return {status: 0, error: String(e)};
                            }
                        }""", discord_token)

                        if not oauth_result or oauth_result.get("status") != 200:
                            self.bot.log("WARN", "BrowserUse: OAuth fetch failed: " + str(oauth_result))
                            oauth_failed = True
                        else:
                            redirect_url = oauth_result.get("data", {}).get("location")
                            if redirect_url:
                                try:
                                    await page.goto(redirect_url, wait_until="domcontentloaded", timeout=30000)
                                except Exception:
                                    pass
                    except Exception as e:
                        self.bot.log("WARN", "BrowserUse: OAuth step warning: " + str(e))
                        oauth_failed = True

                    await asyncio.sleep(2)

                    self.bot.log("SYS", "BrowserUse: Opening captcha page...")
                    try:
                        await page.goto(self.captcha_url, wait_until="domcontentloaded", timeout=45000)
                    except Exception as e:
                        self.bot.log("WARN", "BrowserUse: Captcha page load warning: " + str(e))

                    self.bot.log("SYS", "BrowserUse: Waiting for captcha to be solved...")

                    token = None
                    for _ in range(55):
                        await asyncio.sleep(2)

                        if not self._still_solving():
                            solved_via_dm = True
                            break

                        try:
                            token = await page.evaluate("""() => {
                                const ta = document.querySelector('textarea[name="h-captcha-response"]');
                                if (ta && ta.value && ta.value.length > 20) return ta.value;
                                const inp = document.querySelector('input[name="h-captcha-response"]');
                                if (inp && inp.value && inp.value.length > 20) return inp.value;
                                if (window.hcaptcha && window.hcaptcha.getResponse) {
                                    try {
                                        const r = window.hcaptcha.getResponse();
                                        if (r && r.length > 20) return r;
                                    } catch(err) {}
                                }
                                return null;
                            }""")
                        except Exception:
                            token = None
                        if token:
                            break

                    try:
                        await browser.close()
                    except Exception:
                        pass

                    if solved_via_dm:
                        self._note_success(country)
                        self.bot.log("SUCCESS", "BrowserUse: DM verification confirmed — captcha solved.")
                        return True

                    if token:
                        self._note_success(country)
                        self.bot.log("SUCCESS", "BrowserUse: hCaptcha solved, submitting to OwO...")
                        if await self._verify_with_owo(token):
                            return True
                        else:
                            self.bot.log("ERROR", "BrowserUse: OwO rejected the token, retrying...")
                    else:
                        if oauth_failed:
                            self._note_fail(country)
                        self.bot.log("WARN", "BrowserUse: No hCaptcha token detected, retrying...")

            except Exception as e:
                self.bot.log("ERROR", "BrowserUse attempt " + str(attempt + 1) + " failed: " + str(e))
            finally:
                if session_id:
                    await self._stop_browser(session_id)
                if attempt < retries - 1:
                    await asyncio.sleep(5)

        return False

    async def _verify_with_owo(self, token):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://owobot.com/api/captcha/verify",
                    json={"token": token},
                    headers={
                        "Referer": "https://owobot.com/captcha",
                        "Origin": "https://owobot.com",
                        "Accept": "application/json, text/plain, */*",
                        "Content-Type": "application/json",
                    }
                ) as resp:
                    if resp.status == 200:
                        self.bot.log("SUCCESS", "BrowserUse: OwO verification succeeded!")
                        return True
                    err = await resp.text()
                    self.bot.log("ERROR", "BrowserUse verify failed: " + str(resp.status) + " - " + err)
                    return False
        except Exception as e:
            self.bot.log("ERROR", "BrowserUse verify exception: " + str(e))
            return False