#INFORMATION IMPORTANTE : L'AGENT NE DEMANDERA PAS LE NUMERO DE l'APPELANT
#EN EFFET, IL (l'AGENT PEUT DIRECTEMENT SAVOI QUI APPEL)
from system_prompt import SYSTEM_PROMPT, GREETINGS
import os
import logging
from twilio.rest import Client
from dotenv import load_dotenv 
from livekit.agents import AgentServer, AgentSession, Agent, room_io
from livekit.plugins import openai, noise_cancellation,gladia, ai_coustics
from livekit.plugins.openai import realtime
from openai.types.beta.realtime.session import TurnDetection
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
    ConversationItemAddedEvent)

load_dotenv()

def Kalli() -> None:
    return Agent(instructions=SYSTEM_PROMPT)

server = AgentServer()

@server.rtc_session(agent_name="Kalli")
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
            )
        )
    )
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
        instructions=f"""Salue le client en disant exactement : '{GREETINGS.strip()}'. 
        Enchaîne immédiatement en demandant son nom et son prénom pour commencer le diagnostic."""
    )

if __name__ == "__main__":
    agents.cli.run_app(server)