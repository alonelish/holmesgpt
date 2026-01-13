import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Dict, Optional, Union

if TYPE_CHECKING:
    from holmes.config import Config
    from holmes.core.models import ChatRequest, ChatResponse
    from holmes.core.supabase_dal import SupabaseDal
    from fastapi.responses import StreamingResponse

from holmes import get_version
from holmes.core.models import ChatRequest, ChatResponse

# Type definition for the chat function signature
ChatFunction = Callable[[ChatRequest], Union["ChatResponse", "StreamingResponse"]]


class ScheduledPromptsExecutor:
    """
    Executor that periodically checks for pending scheduled prompt runs and executes them.
    Runs in a background thread, checking every minute for pending runs.
    """

    def __init__(
        self,
        dal: "SupabaseDal",
        config: "Config",
        chat_function: ChatFunction,
    ):
        """
        Initialize the ScheduledPromptsExecutor.

        Args:
            dal: SupabaseDAL instance for database access
            config: Config instance (kept for compatibility, may be used in future)
            chat_function: The chat function to call with ChatRequest, signature: def chat(chat_request: ChatRequest) -> ChatResponse | StreamingResponse
        """
        self.dal = dal
        self.config = config
        self.chat_function = chat_function
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """Start the executor in a background thread."""
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
        """Stop the executor."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logging.info("ScheduledPromptsExecutor stopped")

    def _run_loop(self):
        """Main loop that checks for pending runs every minute."""
        while self.running:
            try:
                self._check_and_execute_pending_run()
            except Exception as e:
                logging.exception(
                    f"Error in ScheduledPromptsExecutor loop: {e}", exc_info=True
                )

            # Sleep for 60 seconds before next check
            for _ in range(60):
                if not self.running:
                    break
                time.sleep(1)

    def _check_and_execute_pending_run(self):
        """Check for a pending run and execute it if found."""
        pending_run = self.dal.get_one_pending_run()
        if not pending_run:
            return

        run_id = pending_run.get("id")
        if not run_id:
            logging.error("Pending run found but missing id field")
            return

        prompt = pending_run.get("prompt", "")
        model_name = pending_run.get("model_name", "")

        if not prompt:
            error_msg = "No prompt provided"
            logging.warning(f"Pending run {run_id} has no prompt, marking as failed")
            self.dal.update_run_status(run_id, "failed", error_msg)
            return

        if not model_name:
            error_msg = "No model_name provided"
            logging.warning(
                f"Pending run {run_id} has no model_name, marking as failed"
            )
            self.dal.update_run_status(run_id, "failed", error_msg)
            return

        # Validate that the model exists
        available_models = self.config.get_models_list()
        if model_name not in available_models:
            error_msg = f"Model '{model_name}' not found in available models: {available_models}"
            logging.warning(
                f"Pending run {run_id} has invalid model_name '{model_name}', marking as failed"
            )
            self.dal.update_run_status(run_id, "failed", error_msg)
            return

        logging.info(
            f"Found pending run {run_id}, executing with model {model_name}"
        )

        # Update status to running
        if not self.dal.update_run_status(run_id, "running"):
            error_msg = "Failed to update run status to running"
            logging.error(f"{error_msg} for run {run_id}")
            self.dal.update_run_status(run_id, "failed", error_msg)
            return

        # Get parent_id for result tracking
        parent_id = pending_run.get("parent_id")

        # Execute the prompt
        try:
            response = self._execute_prompt(run_id, prompt, model_name, parent_id)
            # Mark as completed on success (clear any previous error message)
            self.dal.update_run_status(run_id, "completed", None)
            logging.info(f"Successfully completed run {run_id}")
        except Exception as e:
            error_msg = str(e)
            logging.exception(
                f"Error executing prompt for run {run_id}: {error_msg}", exc_info=True
            )
            # Mark as failed on error with error message
            self.dal.update_run_status(
                run_id, "failed", f"Error: {error_msg[:500]}"
            )

    def _execute_prompt(
        self, run_id: str, prompt: str, model_name: str, parent_id: Optional[str]
    ):
        """
        Execute a prompt by building a ChatRequest and calling the chat function.

        Args:
            run_id: The ID of the run being executed
            prompt: The prompt text to execute
            model_name: The model name to use for the LLM
            parent_id: The parent job definition ID

        Returns:
            ChatResponse from the chat function
        """
        # Build ChatRequest with the prompt and model from the database
        chat_request = ChatRequest(
            ask=prompt,
            model=model_name,
            conversation_history=None,  # Scheduled prompts start fresh
            stream=False,  # Scheduled prompts are always non-streaming
            additional_system_prompt=None,
        )

        # Call the chat function
        response = self.chat_function(chat_request)

        # Save result to HolmesResults table (only for ChatResponse, not StreamingResponse)
        if isinstance(response, ChatResponse):
            self.dal.insert_holmes_result(
                job_id=run_id,
                job_definition_id=parent_id,
                data=response.model_dump(),
                version=get_version(),
            )

        return response

