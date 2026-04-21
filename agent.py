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
from google import genai
from google.genai import types
from pydantic import BaseModel
from typing import Optional


load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("garage-agent")


class ClientInfo(BaseModel):
    prenom: str
    nom: Optional[str] = None
    marque_modele_annee: str
    problème: str
    urgence: str
    date_souhaitee_rdv: Optional[str] = None
    numero_suivi: Optional[str] = None


def call_summary(transcription_file: str = "transcription.txt", phone_number: str = "Non spécifié") -> str:
    client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    with open(transcription_file, "r", encoding="utf-8") as file:
        data = file.read()
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=f"{SUMMARY}\n\nTranscription:\n{data}",
        config=types.GenerateContentConfig(
            system_instruction=SUMMARY,
            response_mime_type="application/json",
            response_schema=ClientInfo.model_json_schema()
        )
    )
    client_info = ClientInfo.model_validate_json(response.text)
    formatted_message = (
        f"Rapport de l'appel :\n\n"
        f"Résumé de la demande du client\n"
        f"Nom: {client_info.prenom} {client_info.nom or ''}\n"
        f"Numéro téléphone: {phone_number}\n"
        f"Information Véhicule Global: {client_info.marque_modele_annee}\n"
        f"Problème: {client_info.problème}\n"
        f"Urgence: {client_info.urgence}\n"
        f"Date Souhaité RDV: {client_info.date_souhaitee_rdv or 'Non spécifiée'}"
    )
    return formatted_message


def Alex() -> Agent:
    return Agent(instructions=SYSTEM_PROMPT)

server = AgentServer()


@server.rtc_session(agent_name="alex_garage")
async def garage_agent(ctx: agents.JobContext):
    gladia_key = os.environ.get("GLADIA_API_KEY")
    phone_number = "unknown"
    
    
    with open("transcription.txt", "w", encoding="utf-8") as f:
        f.write("")

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
        if not isinstance(transcript.item, ChatMessage):
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open("transcription.txt", "a", encoding="utf-8") as file:
                if transcript.item.role == "assistant":
                    content = transcript.item.text_content or ""
                    print(f"Alex : {content}")
                    file.write(f"{timestamp} Alex : {content}\n")
                elif transcript.item.role == "user":
                    content = transcript.item.text_content or ""
                    print(f"Appelant : {content}")
                    file.write(f"{timestamp} Appellant : {content}\n")
        except Exception as e:
            logger.error(f"Failed to write to transcription.txt: {e}")

    caller = await ctx.wait_for_participant()
    if caller.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
        phone_number = caller.attributes.get("sip.phoneNumber", "unknown")
        logger.info(f"{phone_number} is calling...")
    async def send_summary():
        logger.info("Appel terminé. Génération du résumé...")
        try:
            if os.path.exists("transcription.txt"):
                summary_message = call_summary("transcription.txt", phone_number)
                account_sid = os.environ.get("ACCOUNT_SID")
                auth_token = os.environ.get("AUTH_TOKEN")
                if account_sid and auth_token:
                    twilio_client = Client(account_sid, auth_token)
                    twilio_client.messages.create(
                        body=summary_message,
                        from_=os.environ.get("SENDER_PHONE_NUMBER"),
                        to=os.environ.get("RECEIVER_PHONE_NUMBER")
                    )
                    logger.info("Résumé envoyé avec succès")
                else:
                    logger.error("Variables d'environnement Twilio manquantes")
            else:
                logger.warning("Fichier transcription.txt non trouvé")
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi du résumé: {e}")
    ctx.add_shutdown_callback(send_summary)

    await session.start(
        room=ctx.room,
        agent=Alex(),
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
