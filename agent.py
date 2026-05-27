# INFORMATION IMPORTANTE : L'AGENT NE DEMANDERA PAS LE NUMERO DE l'APPELANT
# EN EFFET, IL (l'AGENT PEUT DIRECTEMENT SAVOI QUI APPEL)
import asyncio
import os
import datetime
import logging
from dotenv import load_dotenv
from openai import OpenAI
from openai.types.beta.realtime.session import TurnDetection
from livekit import agents, rtc, api
from livekit.agents import (
    JobProcess,
    TurnHandlingOptions,
    RunContext,
    UserStateChangedEvent,
    mcp,
    AgentServer,
    AgentSession,
    Agent,
    JobContext,
    room_io,
    RunContext,
    function_tool,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
    get_job_context)
from livekit.plugins import openai, noise_cancellation, gladia, ai_coustics,cartesia,google
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
from livekit.agents import BackgroundAudioPlayer, AudioConfig, BuiltinAudioClip,inference
from livekit.agents.llm import ImageContent
import base64

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("garage-agent")

with open("tarifs.jpg", "rb") as f:
    tarifs = f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode('utf-8')}"


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

zapier_mcp = mcp.MCPServerHTTP(
    allowed_tools=["execute_zapier_write_action","execute_zapier_read_action","list_enabled_zapier_actions"],
    url=zapier_url,
    transport_type="streamable_http",
    timeout = 120.0,
    client_session_timeout_seconds=120.0
)


calendar_tool = mcp.MCPToolset(id="zapier", mcp_server=zapier_mcp)


class Alex(Agent):
    def __init__(self, phone_number: str, *, chat_ctx: Optional[ChatContext] = None):
        
        self.phone_number = phone_number
        current_date = datetime.datetime.now().strftime("%A %d %B %Y")
        dynamic_instructions = (
            f"{SYSTEM_PROMPT}\n\n"
            f"### INFORMATIONS TEMPORELLES (OBLIGATOIRE)\n"
            f"- DATE D'AUJOURD'HUI : {current_date}\n"
            f"- FUSEAU HORAIRE : Gatineau, Canada (Eastern Time).\n"
            f"- HEURES D'OUVERTURE : 8h45 à 16h00, du lundi au vendredi.\n"
            f"- Tu es en {datetime.datetime.now().year}. Ne propose jamais de dates passées.\n\n"
            f"### UTILISATION DU CALENDRIER ZAPIER (RÈGLES TECHNIQUES)\n"
            f"- ID CALENDRIER : 'garageroadr@gmail.com' (à utiliser pour 'calendarid' si demandé).\n"
            f"- DÉCOUVERTE (OBLIGATOIRE) : Avant toute lecture/écriture Google Calendar, appelle `list_enabled_zapier_actions` (app: 'Google Calendar').\n"
            f"- PARAMÈTRES (OBLIGATOIRE) : Avant un appel `execute_zapier_read_action`/`execute_zapier_write_action`, récupère les paramètres attendus en rappelant `list_enabled_zapier_actions` avec la key de l'action visée (ex: `find_busy_periods`, `event_v2`, `detailed_event`). Utilise ensuite exactement ces noms de champs.\n"
            f"- VÉRIFICATION DES DISPONIBILITÉS : Utilise `execute_zapier_read_action` avec l'action `find_busy_periods`.\n"
            f"  - instructions (ANGLAIS) : 'Find available slots between 8:45 AM and 4:00 PM EST for the next 7 days.'\n"
            f"- CRÉATION DE RENDEZ-VOUS (OBLIGATOIRE) : Utilise `execute_zapier_write_action` avec l'action `detailed_event` si disponible.\n"
            f"- HEURES (OBLIGATOIRE) : Ne laisse jamais Zapier/deviner des horaires. Passe toujours un début + une fin (ou une durée) avec des datetimes explicites en fuseau horaire de Gatineau.\n"
            f"- DURÉE (OBLIGATOIRE) : Par défaut, un rendez-vous = 60 minutes (diagnostic). N'utilise 30 minutes QUE si le client le demande explicitement ou si une durée précise est confirmée.\n"
            f"- CONFLITS (OBLIGATOIRE) : Avant de créer, relance `find_busy_periods` sur la fenêtre exacte du rendez-vous pour vérifier l'absence de conflit.\n"
            f"- RÈGLE ANTI-DOUBLONS (OBLIGATOIRE) : Ne dis jamais 'je ne peux pas empêcher les doublons'. Tu gères les doublons en vérifiant les conflits avec `find_busy_periods`. Si tu ne peux pas vérifier (erreur outil / action manquante), tu n'essaies pas de créer et tu dis qu'un humain rappellera.\n"
            f"- INTERDIT (OBLIGATOIRE) : Ne demande jamais au client de vérifier lui-même son calendrier.\n"
            f"- VÉRIFICATION POST-CRÉATION (OBLIGATOIRE) : Après création, vérifie que l'événement existe vraiment en lecture (priorité: `event_by_id` si un ID est retourné, sinon `event_v2` sur la fenêtre horaire). Tu ne dis jamais 'c'est confirmé' tant que cette vérification ne renvoie pas l'événement.\n"
            f"- FOLLOW-UP (OBLIGATOIRE) : Si un outil renvoie `followUpQuestion`, tu DOIS poser la question au client et attendre sa réponse. Ne confirme rien avant d'avoir un succès final.\n"
            f"- EN CAS D'ÉCHEC : Si Zapier répond 'Action not found' et fournit `availableActions`, choisis une action depuis cette liste (priorité: `find_busy_periods`, puis `event_v2`, puis `detailed_event`) et retente une seule fois.\n"
            f"- INTERDICTION : Ne mets jamais un champ `output` dans les paramètres envoyés à Zapier.\n"
            f"  - summary: 'NomClient {phone_number}'\n"
            f"  - description: Détails du véhicule en français (ex: 'Toyota Civic 2016 - Pneu crevé')\n"
            f"  - transparency: 'opaque'\n"
            f"  - visibility: 'private'\n"
            f"  - instructions (ANGLAIS) : 'Create event at [Heure] Gatineau time (EST/EDT). Ensure no double booking.'\n\n"
            f"### ACTION IMMÉDIATE (CRITIQUE)\n"
            f"Dès que tu annonces 'Je regarde le calendrier' ou 'Un instant', tu DOIS générer l'appel d'outil IMMÉDIATEMENT dans le même tour. N'attends pas de réponse de l'utilisateur après avoir dit que tu vas chercher."
        )
        super().__init__(instructions=dynamic_instructions, tools=[calendar_tool], chat_ctx=chat_ctx)


server = AgentServer()

@server.rtc_session(agent_name="alex_garage")
async def garage_agent(ctx: agents.JobContext):

    garage_context = ChatContext()
    garage_context.add_message(
        role = "user",content=["Voici une image des tarifs du garage",ImageContent(image=tarifs)]
    )
    
    phone_number = "Inconnu"
   
    with open("transcription.txt", "w", encoding="utf-8") as f:
        f.write("")
    session = AgentSession(
        


      
        
    
        user_away_timeout=15.0,
        tts=inference.TTS(
            model="cartesia/sonic-3",
            language="fr",
            voice=os.environ.get("VOICE_ID")
        ),
     
        
      
       
        llm=realtime.RealtimeModel(
        model="gpt-realtime-1.5",
        modalities=["TEXT"],
        turn_detection=TurnDetection(
            type="server_vad",
            threshold=0.7,
            prefix_padding_ms=300,
            silence_duration_ms=400,
            create_response=True,
            interrupt_response=False
        ),
    )
    )

    @session.on("user_state_changed")
    def end_call(user_presence : UserStateChangedEvent) : 
        if user_presence.new_state == "away":
            asyncio.create_task(hangup_call())
           
            
    
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
        agent=Alex(phone_number,chat_ctx=garage_context),
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
