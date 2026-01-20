import logging
import os
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional, Union

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from fastapi.responses import StreamingResponse
    from holmes.config import Config
    from holmes.core.models import ChatRequest, ChatResponse
    from holmes.core.supabase_dal import SupabaseDal

from holmes import get_version
from holmes.core.models import ChatRequest, ChatResponse
from holmes.core.supabase_dal import RunStatus

ChatFunction = Callable[[ChatRequest], Union["ChatResponse", "StreamingResponse"]]


class ScheduledPrompt(BaseModel):
    id: str
    scheduled_prompt_definition_id: Optional[str] = None
    account_id: str
    cluster_name: str
    model_name: str
    prompt: Union[str, dict]
    status: str
    msg: Optional[str] = None
    created_at: datetime
    last_heartbeat_at: datetime
    metadata: Optional[dict] = None


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
                payload = self.dal.claim_scheduled_prompt_run(self.holmes_id)
                if not payload:
                    time.sleep(60)
                    continue

                try:
                    sp = ScheduledPrompt(**payload)
                except ValidationError as exc:
                    logging.exception(
                        "Skipping invalid scheduled prompt payload: %s",
                        exc,
                        exc_info=True,
                    )
                    continue

                try:
                    self._execute_scheduled_prompt(sp)
                except Exception as exc:
                    logging.exception(
                        "Error executing scheduled %s prompt: %s",
                        sp.id,
                        exc,
                        exc_info=True,
                    )
            except Exception as exc:
                logging.exception(
                    "Error in ScheduledPromptsExecutor loop: %s", exc, exc_info=True
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

        try:
            self._execute_prompt(sp)
            logging.info("Successfully completed run %s", run_id)
        except Exception as exc:
            error_msg = str(exc)
            logging.exception(
                "Error executing prompt for run %s: %s",
                run_id,
                error_msg,
                exc_info=True,
            )
            self.dal.finish_scheduled_prompt_run(
                status=RunStatus.FAILED,
                result={"error": error_msg},
                run_id=run_id,
                scheduled_prompt_definition_id=sp.scheduled_prompt_definition_id,
                version=get_version(),
                metadata=sp.metadata,
            )

    def _execute_prompt(
        self,
        sp: ScheduledPrompt,
    ):
        start = time.perf_counter()
        chat_request = ChatRequest(
            ask=self._extract_prompt_text(sp.prompt),
            model=sp.model_name,
            conversation_history=None,
            stream=False,
            additional_system_prompt=None,
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
