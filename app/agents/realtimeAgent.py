from dotenv import load_dotenv
from dataclasses import dataclass
from livekit import agents
from livekit.agents import (
    AgentSession,
    Agent,
    RoomInputOptions,
    function_tool,
    get_job_context,
    RunContext,
    ChatContext,
    ChatMessage,
)
from livekit.protocol.room import DeleteRoomRequest
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins import openai, noise_cancellation, silero

load_dotenv()


@dataclass
class MySessionInfo:
    user_name: str | None = None
    age: int | None = None


class IntakeAgent(Agent):
    def __init__(self):
        super().__init__(
            instructions="""Your are an intake agent. Learn the user's name and age."""
        )

    @function_tool()
    async def record_name(self, context: RunContext[MySessionInfo], name: str):
        """Use this tool to record the user's name."""
        context.userdata.user_name = name
        return self._handoff_if_done()

    @function_tool()
    async def record_age(self, context: RunContext[MySessionInfo], age: int):
        """Use this tool to record the user's age."""
        context.userdata.age = age
        return self._handoff_if_done()

    def _handoff_if_done(self):
        if self.session.userdata.user_name and self.session.userdata.age:
            return Assistant()
        else:
            return None


class Assistant(Agent):
    def __init__(self):
        super().__init__(
            instructions="You are a helpful voice AI assistant. Always respond in Spanish. Greet the user with a warm welcome. At the beginning of the conversation, ask for the user's name and age.",
        )

    # async def on_enter(self) -> None:
    #     await self.session.generate_reply(
    #         instructions="Greet the user with a warm welcome",
    #     )

    async def on_exit(self) -> None:
        userdata: MySessionInfo = self.session.userdata
        await self.session.generate_reply(
            instructions=f"Tell {userdata.user_name} a friendly goodbye before you exit."
        )

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        userdata: MySessionInfo = self.session.userdata
        turn_ctx.add_message(role="assistant", content=userdata)
        await self.update_chat_ctx(turn_ctx)


class ConsentCollector(Agent):
    def __init__(self):
        super().__init__(
            instructions="""Your are a voice AI agent with the singular task to collect positive 
            recording consent from the user. If consent is not given, you must end the call.Always respond in Spanish"""
        )

    async def on_enter(self) -> None:
        await self.session.say("May I record this call for quality assurance purposes?")

    @function_tool()
    async def on_consent_given(self):
        """Use this tool to indicate that consent has been given and the call may proceed."""

        # Perform a handoff, immediately transfering control to the new agent
        return Assistant(chat_ctx=self.session._chat_ctx)

    @function_tool()
    async def end_call(self) -> None:
        """Use this tool to indicate that consent has not been given and the call should end."""
        await self.session.say("Thank you for your time, have a wonderful day.")
        job_ctx = get_job_context()
        await job_ctx.api.room.delete_room(DeleteRoomRequest(room=job_ctx.room.name))


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession[MySessionInfo](
        userdata=MySessionInfo(),
        llm=openai.realtime.RealtimeModel(voice="coral"),
        turn_detection=MultilingualModel(),
        vad=ctx.proc.userdata["vad"],
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            # LiveKit Cloud enhanced noise cancellation
            # - If self-hosting, omit this parameter
            # - For telephony applications, use `BVCTelephony` for best results
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

    # await session.generate_reply(
    #     instructions="Greet the user and offer your assistance."
    # )


def prewarm(proc: agents.JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


if __name__ == "__main__":
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm)
    )
