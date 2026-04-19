# INFORMATION IMPORTANTE : L'AGENT NE DEMANDERA PAS LE NUMERO DE l'APPELANT
# EN EFFET, IL (l'AGENT PEUT DIRECTEMENT SAVOI QUI APPEL)




import os
import datetime
import logging

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.beta.realtime.session import TurnDetection
from twilio.rest import Client
from livekit import agents, rtc, api
from livekit.agents import (
    AgentServer,
    AgentSession,
    Agent,
    room_io,
    RunContext,
    function_tool,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
)
from livekit.plugins import openai, noise_cancellation, gladia, ai_coustics
from livekit.plugins.openai import realtime

from system_prompt import SYSTEM_PROMPT, GREETINGS, SUMMARY


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("garage-agent")


def Kalli() -> Agent:
    return Agent(instructions=SYSTEM_PROMPT)


server = AgentServer()


@server.rtc_session(agent_name="alex_garage")
async def garage_agent(ctx: agents.JobContext):
    gladia_key = os.environ.get("GLADIA_API_KEY")
    session = AgentSession(
        stt=gladia.STT(api_key=gladia_key, languages=["fr", "en"]),
        llm=openai.realtime.RealtimeModel(
            model="gpt-realtime-mini-2025-12-15",
            voice="coral",
            turn_detection=TurnDetection(
                type="semantic_vad",
                eagerness="auto",
                interrupt_response=True,
            ),
        ),
    )

    @session.on("conversation_item_added")
    def transcription(transcript: ConversationItemAddedEvent):
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open("transcription.txt", "a") as file:
            if transcript.item.role == "assistant":
                print(f"Alex : {transcript.item.text_content}")
                file.write(f"{timestamp} Alex : {transcript.item.text_content}")
            if transcript.item.role == "user":
                print(f"Appelant : {transcript.item.text_content}")
                file.write(f"{timestamp} Appellant : {transcript.item.text_content}")

    caller = await ctx.wait_for_participant()
    if caller.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        phone_number = caller.attributes.get("sip.phoneNumber", "unknown")
        logger.info(f"{phone_number} is calling...")

    async def summarize_and_send():
        pass  # ajouter la logique pour permettre à l'agent de pouvoir résumer l'appel et envoyer le résumer sur Whatsapp

    ctx.add_shutdown_callback(summarize_and_send)

    await session.start(
        room=ctx.room,
        agent=Kalli(),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_L
                ),
            ),
        ),
    )
    await session.generate_reply(
        instructions=(
            f"Salue le client en disant exactement : '{GREETINGS.strip()}'. "
            "Enchaîne immédiatement en demandant son nom et son prénom pour commencer le diagnostic."
        )
    )


if __name__ == "__main__":
    agents.cli.run_app(server)
