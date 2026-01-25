import json
import logging
import os
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional, Union
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from pydantic import ValidationError

from holmes import get_version
from holmes.common.env_vars import (
    ADDITIONAL_SYSTEM_PROMPT_URL,
    SCHEDULED_PROMPTS_POLL_INTERVAL_SECONDS,
)
from holmes.core.models import ChatRequest, ChatResponse
from holmes.core.scheduled_prompts.heartbeat_tracer import (
    ScheduledPromptsHeartbeatTracer,
)
from holmes.core.scheduled_prompts.models import ScheduledPrompt
from holmes.core.supabase_dal import RunStatus

# to prevent circular imports due to type hints
if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse

    from holmes.config import Config
    from holmes.core.supabase_dal import SupabaseDal

ChatFunction = Callable[[ChatRequest], Union["ChatResponse", "StreamingResponse"]]


class ScheduledPromptsExecutor:
    def __init__(
        self,
        dal: "SupabaseDal",
        config: "Config",
        chat_function: ChatFunction,
    ):
        self.dal = dal
        self.config = config
        self.chat_function = chat_function
        self.running = False
        self.thread: Optional[threading.Thread] = None
        # this is pod name in kubernetes
        self.holmes_id = os.environ.get("HOSTNAME") or str(os.getpid())

    def start(self):
        if not self.dal.enabled:
            logging.info(
                "ScheduledPromptsExecutor not started - Supabase DAL not enabled"
            )
            return

        if self.running:
            logging.warning("ScheduledPromptsExecutor is already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        logging.info("ScheduledPromptsExecutor started")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logging.info("ScheduledPromptsExecutor stopped")

    def _run_loop(self):
        while self.running:
            try:
                self._process_next_prompt()
            except Exception as exc:
                logging.exception(
                    "Error in ScheduledPromptsExecutor loop: %s", exc, exc_info=True
                )

    def _process_next_prompt(self):
        """Process the next scheduled prompt, if available."""
        payload = self.dal.claim_scheduled_prompt_run(self.holmes_id)
        if not payload:
            time.sleep(SCHEDULED_PROMPTS_POLL_INTERVAL_SECONDS)
            return

        try:
            sp = ScheduledPrompt(**payload)
        except ValidationError as exc:
            logging.warning(f"{str(payload)} is not a valid ScheduledPrompt")
            logging.exception(
                "Skipping invalid scheduled prompt payload: %s",
                exc,
                exc_info=True,
            )
            return

        self.dal.update_run_status(run_id=sp.id, status=RunStatus.RUNNING)

        try:
            self._execute_scheduled_prompt(sp)
        except Exception as exc:
            logging.exception(
                "Error executing scheduled %s prompt: %s",
                sp.id,
                exc,
                exc_info=True,
            )
            self._finish_run(
                status=RunStatus.FAILED,
                result={"error": str(exc)},
                sp=sp,
            )

    def _execute_scheduled_prompt(self, sp: ScheduledPrompt):
        run_id = sp.id
        available_models = self.config.get_models_list()
        if sp.model_name not in available_models:
            error_msg = f"Model '{sp.model_name}' not found in available models: {available_models}"
            logging.warning(
                "Pending run %s has invalid model_name '%s', marking as failed",
                run_id,
                sp.model_name,
            )
            self._finish_run(
                status=RunStatus.FAILED,
                result={"error": error_msg},
                sp=sp,
            )
            return

        logging.info(
            "Found pending run %s, executing with model %s", run_id, sp.model_name
        )
        self._execute_prompt(sp)
        logging.info("Successfully completed run %s", run_id)

    def _execute_prompt(
        self,
        sp: ScheduledPrompt,
    ):
        start = time.perf_counter()
        additional_system_prompt = self._fetch_additional_system_prompt(
            sp.prompt.get("additional_system_prompt")
        )

        # Create heartbeat tracer
        heartbeat_tracer = ScheduledPromptsHeartbeatTracer(sp=sp, dal=self.dal)
        heartbeat_span = heartbeat_tracer.start_trace(name="scheduled_prompt_execution")

        # Create chat request with heartbeat span
        chat_request = ChatRequest(
            ask=self._extract_prompt_text(sp.prompt),
            model=sp.model_name,
            conversation_history=None,
            stream=False,
            additional_system_prompt=additional_system_prompt,
            trace_span=heartbeat_span,
        )

        response = self.chat_function(chat_request)
        duration_seconds = time.perf_counter() - start

        result_data = (
            response.model_dump() if isinstance(response, ChatResponse) else {}
        )

        if isinstance(response, ChatResponse):
            response.metadata = dict(response.metadata or {})
            response.metadata["duration_seconds"] = duration_seconds

        self._finish_run(status=RunStatus.COMPLETED, result=result_data, sp=sp)

        return response

    def _fetch_additional_system_prompt(
        self, fallback: Optional[str] = None
    ) -> Optional[str]:
        """
        Fetches the additional system prompt from the Robusta platform.
        Falls back to the provided value if the fetch fails.
        """
        try:
            with urlopen(ADDITIONAL_SYSTEM_PROMPT_URL, timeout=10) as resp:
                if resp.status != 200:
                    logging.warning(
                        "Failed to fetch additional system prompt, status: %s",
                        resp.status,
                    )
                    return fallback
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("additional_system_prompt", fallback)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            logging.warning(
                "Error fetching additional system prompt, using fallback: %s", exc
            )
            return fallback

    def _finish_run(
        self,
        status: RunStatus,
        result: dict,
        sp: ScheduledPrompt,
    ) -> None:
        self.dal.finish_scheduled_prompt_run(
            status=status,
            result=result,
            run_id=sp.id,
            scheduled_prompt_definition_id=sp.scheduled_prompt_definition_id,
            version=get_version(),
            metadata=sp.metadata,
        )

    def _extract_prompt_text(self, prompt: Union[str, dict]) -> str:
        if isinstance(prompt, dict):
            raw = prompt.get("raw_prompt")
            if raw:
                return raw
        return str(prompt)
