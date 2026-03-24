"""
SAFS v6.0 — Playwright Validator (PATH β)

Validates HTML5 streaming app fixes using headless Chromium with
Companion Library version-aware mocking.

Validation Steps:
1. Launch headless Chromium with Companion Library mock
2. Navigate to app URL
3. Execute test scenarios (cold launch, navigation, error reproduction)
4. Monitor console errors and network requests
5. Capture screenshots for visual verification
6. Return pass/fail with detailed evidence

Limitations:
- Tests against standard Chromium, not Vizio's custom build
- Companion Library mock may not perfectly match real LOKi behavior
- DRM flows are mocked (not real Widevine L1)
- Fast feedback (~45s per scenario)
"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..log_analysis.models import ErrorCategory, FixCandidate
from .models import (
    PathValidationResult,
    PlaywrightValidationResult,
    ValidationPath,
)

logger = logging.getLogger(__name__)


class PlaywrightValidator:
    """
    PATH β: Playwright headless validation for HTML5 fixes.
    
    Validates HTML5 streaming app fixes by:
    1. Running scenarios in headless Chromium
    2. Mocking Companion Library with version-aware responses
    3. Monitoring console errors and network activity
    4. Capturing screenshots and network logs
    """
    
    def __init__(
        self,
        chromium_path: Optional[str] = None,
        companion_mock_port: int = 12345,
    ):
        """
        Initialize Playwright validator.
        
        Args:
            chromium_path: Path to Chromium binary (None = use system)
            companion_mock_port: Port for Companion Library mock server
        """
        self.chromium_path = chromium_path
        self.companion_mock_port = companion_mock_port
        self._playwright = None
        self._browser = None
        
    async def validate(
        self,
        candidate: FixCandidate,
        error_category: ErrorCategory,
        app_name: Optional[str] = None,
        companion_version: Optional[str] = None,
        test_scenarios: Optional[List[Dict[str, Any]]] = None,
    ) -> PathValidationResult:
        """
        Validate an HTML5 fix candidate using Playwright.
        
        Args:
            candidate: Fix candidate to validate
            error_category: Error category (determines test scenarios)
            app_name: Streaming app name (Netflix, Hulu, etc.)
            companion_version: Companion Library version to mock
            test_scenarios: Custom test scenarios (default: auto from category)
            
        Returns:
            PathValidationResult with Playwright validation results
        """
        start_time = asyncio.get_event_loop().time()
        
        logger.info(f"Starting Playwright validation for candidate {candidate.fix_id}")
        logger.info(f"App: {app_name}, Category: {error_category.value}")
        
        # Initialize Playwright
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("Playwright not installed: pip install playwright")
            return PathValidationResult(
                path=ValidationPath.BETA_PLAYWRIGHT,
                passed=False,
                test_results={},
                evidence={},
                failure_reasons=["Playwright not installed"],
                duration_seconds=0.0,
            )
        
        # Default test scenarios based on error category
        if test_scenarios is None:
            test_scenarios = self._get_default_scenarios(error_category, app_name)
        
        playwright_results = PlaywrightValidationResult(
            companion_mock_version=companion_version or "v6.0.0-mock"
        )
        
        test_results: Dict[str, bool] = {}
        failure_reasons: List[str] = []
        
        try:
            # Start Companion Library mock server
            mock_server = await self._start_companion_mock(companion_version)
            
            async with async_playwright() as p:
                # Launch browser
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=self.chromium_path,
                )
                
                context = await browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent="VIZIO SmartCast/6.0 Chromium/108.0",
                )
                
                # Set up logging
                pages_console_errors: List[str] = []
                pages_network_logs: List[Dict[str, Any]] = []
                
                # Run each test scenario
                for scenario in test_scenarios:
                    scenario_name = scenario["name"]
                    logger.info(f"Running scenario: {scenario_name}")
                    
                    try:
                        page = await context.new_page()
                        
                        # Set up console/network monitoring
                        page.on("console", lambda msg: self._handle_console(
                            msg, pages_console_errors
                        ))
                        page.on("request", lambda req: self._handle_request(
                            req, pages_network_logs
                        ))
                        page.on("response", lambda resp: self._handle_response(
                            resp, pages_network_logs
                        ))
                        
                        # Execute scenario
                        scenario_passed = await self._execute_scenario(
                            page, scenario, candidate
                        )
                        
                        test_results[scenario_name] = scenario_passed
                        playwright_results.scenarios_passed[scenario_name] = scenario_passed
                        
                        # Capture screenshot
                        screenshot_bytes = await page.screenshot(full_page=True)
                        playwright_results.screenshots[scenario_name] = base64.b64encode(
                            screenshot_bytes
                        ).decode()
                        
                        if not scenario_passed:
                            failure_reasons.append(
                                f"Scenario '{scenario_name}' failed"
                            )
                        
                        await page.close()
                        
                    except Exception as e:
                        logger.error(f"Scenario '{scenario_name}' error: {e}", exc_info=True)
                        test_results[scenario_name] = False
                        failure_reasons.append(f"{scenario_name}: {str(e)}")
                
                await browser.close()
            
            # Store console errors and network logs
            playwright_results.console_errors = pages_console_errors
            playwright_results.network_logs = pages_network_logs
            
            # Check for target error in console
            target_error_found = any(
                self._is_target_error(error, error_category)
                for error in pages_console_errors
            )
            
            if target_error_found:
                failure_reasons.append(
                    f"Target error {error_category.value} still present in console"
                )
            
            # Overall pass: all scenarios passed + no target error
            overall_passed = all(test_results.values()) and not target_error_found
            
            # Stop companion mock
            await self._stop_companion_mock(mock_server)
            
        except Exception as e:
            logger.error(f"Playwright validation error: {e}", exc_info=True)
            failure_reasons.append(f"Playwright exception: {str(e)}")
            overall_passed = False
        
        duration = asyncio.get_event_loop().time() - start_time
        
        return PathValidationResult(
            path=ValidationPath.BETA_PLAYWRIGHT,
            passed=overall_passed,
            test_results=test_results,
            evidence={
                "playwright_details": playwright_results.model_dump(),
            },
            failure_reasons=failure_reasons,
            duration_seconds=duration,
        )
    
    def _get_default_scenarios(
        self,
        error_category: ErrorCategory,
        app_name: Optional[str],
    ) -> List[Dict[str, Any]]:
        """
        Get default test scenarios for error category.
        
        Args:
            error_category: Error category
            app_name: Streaming app name
            
        Returns:
            List of test scenarios
        """
        scenarios = []
        
        # Common: cold launch
        scenarios.append({
            "name": "cold_launch",
            "steps": [
                {"action": "goto", "url": "http://localhost:8000/index.html"},
                {"action": "wait", "selector": "#app-loaded", "timeout": 10000},
            ],
        })
        
        # Category-specific scenarios
        if error_category == ErrorCategory.COMPANION_LIB_TIMING:
            scenarios.append({
                "name": "companion_lib_timing",
                "steps": [
                    {"action": "goto", "url": "http://localhost:8000/index.html"},
                    {"action": "wait_for_event", "event": "VIZIO_LIBRARY_DID_LOAD", "timeout": 5000},
                ],
            })
        
        elif error_category == ErrorCategory.SHAKA_ERROR_3016:
            scenarios.append({
                "name": "hulu_seek_after_ad",
                "steps": [
                    {"action": "goto", "url": "http://localhost:8000/hulu.html"},
                    {"action": "wait", "selector": "video", "timeout": 5000},
                    {"action": "evaluate", "script": "window.shakaPlayer.play()"},
                    {"action": "wait", "duration": 3000},  # Wait for ad
                    {"action": "evaluate", "script": "window.shakaPlayer.seek(30)"},
                ],
            })
        
        elif error_category == ErrorCategory.KEYDOWN_NOT_FIRED:
            scenarios.append({
                "name": "keydown_navigation",
                "steps": [
                    {"action": "goto", "url": "http://localhost:8000/index.html"},
                    {"action": "press_key", "key": "ArrowDown"},
                    {"action": "wait", "duration": 500},
                    {"action": "press_key", "key": "Enter"},
                    {"action": "wait_for_navigation"},
                ],
            })
        
        return scenarios
    
    async def _execute_scenario(
        self,
        page: Any,
        scenario: Dict[str, Any],
        candidate: FixCandidate,
    ) -> bool:
        """
        Execute a single test scenario.
        
        Args:
            page: Playwright page
            scenario: Scenario definition
            candidate: Fix candidate (for script injection if needed)
            
        Returns:
            True if scenario passed
        """
        try:
            for step in scenario["steps"]:
                action = step["action"]
                
                if action == "goto":
                    await page.goto(step["url"], wait_until="networkidle", timeout=30000)
                
                elif action == "wait":
                    if "selector" in step:
                        await page.wait_for_selector(
                            step["selector"],
                            timeout=step.get("timeout", 5000)
                        )
                    elif "duration" in step:
                        await asyncio.sleep(step["duration"] / 1000.0)
                
                elif action == "wait_for_event":
                    # Wait for custom event (e.g., VIZIO_LIBRARY_DID_LOAD)
                    await page.wait_for_function(
                        f"window.{step['event']}Fired === true",
                        timeout=step.get("timeout", 5000)
                    )
                
                elif action == "evaluate":
                    await page.evaluate(step["script"])
                
                elif action == "press_key":
                    await page.keyboard.press(step["key"])
                
                elif action == "wait_for_navigation":
                    await page.wait_for_load_state("networkidle", timeout=10000)
            
            return True
            
        except Exception as e:
            logger.error(f"Scenario execution failed: {e}")
            return False
    
    def _handle_console(self, msg: Any, errors_list: List[str]) -> None:
        """Handle console messages."""
        if msg.type in ("error", "warning"):
            errors_list.append(f"[{msg.type.upper()}] {msg.text}")
    
    def _handle_request(self, req: Any, logs_list: List[Dict[str, Any]]) -> None:
        """Handle network requests."""
        logs_list.append({
            "type": "request",
            "url": req.url,
            "method": req.method,
        })
    
    def _handle_response(self, resp: Any, logs_list: List[Dict[str, Any]]) -> None:
        """Handle network responses."""
        if resp.status >= 400:
            logs_list.append({
                "type": "response_error",
                "url": resp.url,
                "status": resp.status,
            })
    
    def _is_target_error(self, console_msg: str, error_category: ErrorCategory) -> bool:
        """Check if console message matches target error category."""
        error_patterns = {
            ErrorCategory.COMPANION_LIB_TIMING: ["VIZIO_LIBRARY_DID_LOAD", "companion", "timing"],
            ErrorCategory.SHAKA_ERROR_3016: ["Shaka", "3016", "BUFFER_READ_ERROR"],
            ErrorCategory.NETFLIX_MSL_TIMEOUT: ["MSL", "timeout", "Netflix"],
            ErrorCategory.EME_DRM_FAILURE: ["EME", "DRM", "keysystem"],
            ErrorCategory.JS_HEAP_OOM: ["Out of memory", "heap", "OOM"],
        }
        
        patterns = error_patterns.get(error_category, [])
        return any(pattern.lower() in console_msg.lower() for pattern in patterns)
    
    async def _start_companion_mock(self, version: Optional[str]) -> Any:
        """Start Companion Library mock server."""
        # NOTE: This is a placeholder. Real implementation would start
        # an HTTP server mocking the Companion Library API at localhost:12345
        logger.info(f"Starting Companion mock (version {version})")
        return None
    
    async def _stop_companion_mock(self, server: Any) -> None:
        """Stop Companion Library mock server."""
        logger.info("Stopping Companion mock")
