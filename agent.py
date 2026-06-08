# INFORMATION IMPORTANTE : L'AGENT NE DEMANDERA PAS LE NUMERO DE l'APPELANT
# EN EFFET, IL (l'AGENT PEUT DIRECTEMENT SAVOI QUI APPEL)
import asyncio
import os
import datetime
from zoneinfo import ZoneInfo
import logging
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.beta.realtime.session import TurnDetection
from livekit import agents, rtc, api
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
    JobContext,
    JobProcess,
    RunContext,
    SessionUsageUpdatedEvent,
    TurnHandlingOptions,
    UserStateChangedEvent,
    function_tool,
    get_job_context,
    mcp,
    room_io,
    text_transforms,
)
from livekit.plugins import openai, noise_cancellation, ai_coustics
from livekit.plugins.turn_detector.multilingual import MultilingualModel
from livekit.plugins.openai import realtime
from system_prompt import SYSTEM_PROMPT, GREETINGS, SUMMARY
from google import genai 
from google.genai import types
from pydantic import BaseModel
from typing import Optional
import json
import smtplib
from email.message import EmailMessage
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip, inference




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

def send_email(message: str) -> str:
    email = EmailMessage()
    email["From"] = os.environ.get("SENDER_MAIL")
    email["To"] = os.environ.get("RECEIVER_MAIL")
    email["Subject"] = "Résumé d'appel - Garage Alex"
    email.set_content(message)
    smtp_server = os.environ.get("SMTP_SERVER")
    port = os.environ.get("PORT")
    password = os.environ.get("PASSWORD")
    username = os.environ.get("SENDER_MAIL")
    with smtplib.SMTP(smtp_server, port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(email)
        return "Email Sent !"


async def hangup_call():
    ctx = get_job_context()
    if ctx is None:
        return

    await ctx.api.room.delete_room(
        api.DeleteRoomRequest(
            room=ctx.room.name,
        )
    )

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
        f"Date et Heure RDV: {client_info.date_souhaitee_rdv or 'Non spécifiées'}"
    )
    return formatted_message


zapier_token = os.environ.get("ZAPIER_TOKEN")
zapier_url = f"https://mcp.zapier.com/api/v1/connect?token={zapier_token}"



async def zapier_result_resolver(ctx: mcp.MCPToolResultContext) -> str:
    """Renvoie le résultat textuel de Zapier pour que l'IA puisse l'analyser."""
    if ctx.result and ctx.result.content:
        # Extraire le texte de chaque item de contenu (TextContent)
        texts = []
        for item in ctx.result.content:
            if hasattr(item, "text"):
                texts.append(item.text)
            elif isinstance(item, dict) and "text" in item:
                texts.append(item["text"])
            else:
                texts.append(str(item))
        return "\n".join(texts)
    return "L'outil a été appelé, mais le retour est vide ou invalide."


zapier_mcp = mcp.MCPServerHTTP(
    tool_result_resolver=zapier_result_resolver,
    url=zapier_url,
    transport_type="streamable_http",
    timeout=120.0,
    client_session_timeout_seconds=120.0,
)

calendar_tool = mcp.MCPToolset(
    id="zapier",
    mcp_server=zapier_mcp,
)


class Alex(Agent):
    def __init__(self, phone_number: str, *, chat_ctx: Optional[ChatContext] = None):
        self.phone_number = phone_number

        formatted_prompt = SYSTEM_PROMPT.format(phone_number=phone_number)

        super().__init__(
            instructions=formatted_prompt,
            tools=[calendar_tool],
            chat_ctx=chat_ctx,
        )



server = AgentServer()

@server.rtc_session(agent_name="alex_garage")
async def garage_agent(ctx: agents.JobContext):



   

   

    phone_number = " Numéro de téléphone inconnu"

    with open("transcription.txt", "w", encoding="utf-8") as f:
        f.write("")
    session = AgentSession(
        
        user_away_timeout=10.0,
        tts=inference.TTS(
            model="cartesia/sonic-3",
            language="fr",
            voice=os.environ.get("VOICE_ID")
        ),
        llm=realtime.RealtimeModel(
            temperature=0.6,
            tool_choice="auto",
            model="gpt-realtime-1.5",
            modalities=["TEXT"],
            turn_detection=TurnDetection(
                type="semantic_vad",
                eagerness="medium",
                
                create_response=True,
                interrupt_response=True,
            ),
        ),
    )


    def send_report(cost_report: str) -> str:
        email = EmailMessage()
        email["From"] = os.environ.get("SENDER_MAIL")
        email["To"] = os.environ.get("RECEIVER_MAIL")
        email["Subject"] = "Estimations des couts de Alex"
        email.set_content(cost_report)

        smtp_server = os.environ.get("SMTP_SERVER")
        port = (os.environ.get("PORT"))
        password = os.environ.get("PASSWORD")
        username = os.environ.get("SENDER_MAIL")

        with smtplib.SMTP(smtp_server, port) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(email)

        return "Cost estimation report sent !"

        

    
     

    async def cost_estimation():
        logger.info("Envoie du rapport...")
        with open("rapport.txt") as file : 


            for utilisation in session.usage.model_usage:
                durée_en_secondes = utilisation.session_duration
                durée_en_minutes = durée_en_secondes // 60

                report = (
                f"Fournisseur : {utilisation.provider}\n"
                f" Modèle : {utilisation.model}\n"
                f" Token d'entrés : {utilisation.input_tokens}\n"
                f" Token de sortie : {utilisation.output_tokens} \n"
                f" Estimation des minutes du LLM : {durée_en_minutes}\n"
                )

                cost_report = file.write(report)
                send_report(cost_report)
                logger.info("Rapport envoyé...")

    
    ctx.add_shutdown_callback(cost_estimation)
    


       
   

    @session.on("user_state_changed")
    def end_call(user_presence : UserStateChangedEvent) : 
        if user_presence.new_state == "away":
            
            asyncio.create_task(hangup_call())
           
    @session.on("error")
    def on_session_error(e):
        logger.error(f"Erreur de session (non-fatale) : {e}")
           
    
    
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
                result = send_email(summary_message)
                logger.info(f"Résumé envoyé par email : {result}")
            else:
                logger.warning("Fichier transcription.txt non trouvé")
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi du résumé par email: {e}")
    
    ctx.add_shutdown_callback(send_summary)

    await session.start(
        record=True,
        room=ctx.room,
        agent=Alex(phone_number),
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_L
                ),
            ),
        ),
    )

   

    await session.say(text=GREETINGS.strip())

    background_audio = BackgroundAudioPlayer(
        ambient_sound=AudioConfig(BuiltinAudioClip.OFFICE_AMBIENCE, volume=0.9),
        thinking_sound=[
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.9),
        ],
    )
    await background_audio.start(room=ctx.room, agent_session=session)


if __name__ == "__main__":
    agents.cli.run_app(server)
