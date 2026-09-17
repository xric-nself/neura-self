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

Fallback captcha solver via Kernel cloud browser.
Only invoked when the primary BrowserUse flow exhausts all retries.
Uses Kernel's stealth mode (residential egress) + Nonecap hCaptcha extension.
"""

import os
import asyncio
import random
import aiohttp


NONECAP_EXTENSION_ID = os.getenv("KERNEL_NONECAP_EXT_ID", "nix39s6vbt2ft9819xetee1c")


class FallbackSolver:

    def __init__(self, bot):
        self.bot = bot
        self.api_key = os.getenv("KERNEL_API_KEY", "")
        self.oauth_url = "https://discord.com/api/v9/oauth2/authorize?client_id=408785106942164992&response_type=code&redirect_uri=https://owobot.com/api/auth/discord/redirect&scope=identify guilds"
        self.captcha_url = "https://owobot.com/captcha"

    def _still_solving(self):
        return getattr(self.bot, '_solving_captcha', True)

    async def solve_full_flow(self, discord_token, retries=2):
        if not self.api_key:
            self.bot.log("WARN", "Fallback: KERNEL_API_KEY not set — skipping fallback.")
            return False

        try:
            from kernel import Kernel
        except ImportError:
            self.bot.log("WARN", "Fallback: kernel SDK not installed — skipping fallback.")
            return False

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.bot.log("WARN", "Fallback: playwright not installed — skipping fallback.")
            return False

        self.bot.log("SYS", "Fallback: Starting Kernel captcha flow...")

        client = Kernel()

        for attempt in range(retries):
            if not self._still_solving():
                self.bot.log("SUCCESS", "Fallback: DM verification already received — stopping retries.")
                return True

            self.bot.log("SYS", "Fallback: Attempt " + str(attempt + 1) + "/" + str(retries))

            kb = None
            browser = None
            solved_via_dm = False
            oauth_failed = False

            try:
                kb = client.browsers.create(
                    stealth=True,
                    timeout_seconds=300,
                    extensions=[{"id": NONECAP_EXTENSION_ID}],
                )
                session_id = kb.session_id
                cdp_url = kb.cdp_ws_url

                self.bot.log("SYS", "Fallback: Browser created, connecting via CDP...")

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

                    self.bot.log("SYS", "Fallback: Loading owobot.com...")
                    try:
                        await page.goto("https://owobot.com", wait_until="domcontentloaded", timeout=45000)
                    except Exception as e:
                        self.bot.log("WARN", "Fallback: owobot.com load warning: " + str(e))

                    await asyncio.sleep(2)

                    self.bot.log("SYS", "Fallback: Authorizing Discord OAuth...")
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
                            self.bot.log("WARN", "Fallback: OAuth fetch failed: " + str(oauth_result))
                            oauth_failed = True
                        else:
                            redirect_url = oauth_result.get("data", {}).get("location")
                            if redirect_url:
                                try:
                                    await page.goto(redirect_url, wait_until="domcontentloaded", timeout=30000)
                                except Exception:
                                    pass
                    except Exception as e:
                        self.bot.log("WARN", "Fallback: OAuth step warning: " + str(e))
                        oauth_failed = True

                    await asyncio.sleep(2)

                    self.bot.log("SYS", "Fallback: Opening captcha page...")
                    try:
                        await page.goto(self.captcha_url, wait_until="domcontentloaded", timeout=45000)
                    except Exception as e:
                        self.bot.log("WARN", "Fallback: Captcha page load warning: " + str(e))

                    self.bot.log("SYS", "Fallback: Waiting for captcha to be solved...")

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
                        self.bot.log("SUCCESS", "Fallback: DM verification confirmed — captcha solved.")
                        return True

                    if token:
                        self.bot.log("SUCCESS", "Fallback: hCaptcha solved, submitting to OwO...")
                        if await self._verify_with_owo(token):
                            return True
                        else:
                            self.bot.log("ERROR", "Fallback: OwO rejected the token, retrying...")
                    else:
                        if oauth_failed:
                            self.bot.log("WARN", "Fallback: OAuth failed, proxy likely blocked.")
                        else:
                            self.bot.log("WARN", "Fallback: No hCaptcha token detected, retrying...")

            except Exception as e:
                self.bot.log("ERROR", "Fallback attempt " + str(attempt + 1) + " failed: " + str(e))
            finally:
                if kb is not None:
                    try:
                        client.browsers.delete_by_id(kb.session_id)
                    except Exception as e:
                        self.bot.log("WARN", "Fallback: Kernel delete warning: " + str(e))
                if attempt < retries - 1:
                    await asyncio.sleep(5)

        self.bot.log("ERROR", "Fallback: Kernel flow failed after all retries.")
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
                        self.bot.log("SUCCESS", "Fallback: OwO verification succeeded!")
                        return True
                    err = await resp.text()
                    self.bot.log("ERROR", "Fallback verify failed: " + str(resp.status) + " - " + err)
                    return False
        except Exception as e:
            self.bot.log("ERROR", "Fallback verify exception: " + str(e))
            return False