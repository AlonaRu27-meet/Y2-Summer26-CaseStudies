import os
import re
import base64
from anthropic import Anthropic
from dotenv import load_dotenv
from tavily import TavilyClient
from pypdf import PdfReader
from supabase import create_client, Client
   

load_dotenv()
client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
tavily_client = TavilyClient(api_key=os.getenv('TAVILY_API_KEY'))

# Supabase connection — needs SUPABASE_URL and SUPABASE_KEY in your .env file
supabase: Client = create_client(os.getenv('NEXT_PUBLIC_SUPABASE_URL'), os.getenv('NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY'))

try:
    with open("feedback.txt", "r") as changes_file:
        saved_changes = changes_file.read()
except FileNotFoundError:
    saved_changes = ""


# This system message asks Claude to respond in a strict, labeled format so the
# code below can reliably parse out each piece (score, each language's summary/
# translation) and store them in separate Supabase columns.
system_message = """
    You are Known, an intelligent Jerusalemite news and facts expert agent for "0202 - Points of View from Jerusalem".
    Your aim is to take user-submitted news or articles, verify them using real-time internet search results,
    create a 1-minute summary, and provide a context-aware translation into Hebrew, Arabic, and English.

    Your ultimate job is to bridge cultural gaps, make localized information accessible, show opposing
    viewpoints neutrally, and prepare verified content ready for distribution to strategic partners
    (Educational Institutes, NGOs, and Influencers).

    Always:
    - Always be nice, pleasant, and act with deep empathy for cultural sensitivity.
    - Always translate accurately while factoring in local context and political nuances.
    - List the exact verification sources and links found in the search context.

    Never:
    - DO NOT make up facts or links. If no sources are found in the search results provided,
      explicitly mark the story as UNVERIFIED — do not guess.
    - DO NOT take a political side; remain strictly factual and neutral.
    - DO NOT accept offensive, hateful, or violent language.

    You MUST respond using EXACTLY this format, with these exact section headers, so your
    response can be parsed by code. Do not add extra headers or commentary outside this structure.

    [VERIFICATION_STATUS]: VERIFIED or UNVERIFIED
    [RELIABILITY_SCORE]: a number from 1-5
    [SOURCES]: list each verification link on its own line, or write "None found" if unverified
    [SUMMARY_EN]: 1-minute summary in English
    [SUMMARY_HE]: 1-minute summary in Hebrew
    [SUMMARY_AR]: 1-minute summary in Arabic
    [TRANSLATION_EN]: full context-aware translation in English
    [TRANSLATION_HE]: full context-aware translation in Hebrew
    [TRANSLATION_AR]: full context-aware translation in Arabic
    """
system_message = system_message + f"\nHere are past improvements you must remember:\n{saved_changes}"


def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    full_text = ""
    for page in reader.pages:
        full_text += (page.extract_text() or '') + "\n"
    return full_text


def search_internet(info):
    info = info[:400]
    response = tavily_client.search(query=info, max_results=3)
    return response


def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def parse_ai_response(text):
    """Pulls each labeled section out of Claude's structured reply into a dictionary,
    so each piece can be stored in its own Supabase column."""
    fields = {
        'verified': None, 'reliability_score': None, 'sources': None,
        'summary_en': None, 'summary_he': None, 'summary_ar': None,
        'translation_en': None, 'translation_he': None, 'translation_ar': None,
    }

    def grab(label, stop_labels):
        # Builds a regex that grabs everything after [LABEL]: up until the next
        # known section header (or end of text).
        stop_pattern = '|'.join(re.escape(f"[{s}]") for s in stop_labels)
        pattern = rf"\[{label}\]:\s*(.*?)(?={stop_pattern}|$)"
        match = re.search(pattern, text, re.DOTALL)
        return match.group(1).strip() if match else None

    all_labels = ['VERIFICATION_STATUS', 'RELIABILITY_SCORE', 'SOURCES',
                  'SUMMARY_EN', 'SUMMARY_HE', 'SUMMARY_AR',
                  'TRANSLATION_EN', 'TRANSLATION_HE', 'TRANSLATION_AR']

    status = grab('VERIFICATION_STATUS', all_labels[1:])
    fields['verified'] = bool(status and 'UNVERIFIED' not in status.upper())

    score_text = grab('RELIABILITY_SCORE', all_labels[2:])
    try:
        fields['reliability_score'] = int(re.search(r'\d+', score_text).group())
    except (AttributeError, ValueError, TypeError):
        fields['reliability_score'] = None

    fields['sources'] = grab('SOURCES', all_labels[3:])
    fields['summary_en'] = grab('SUMMARY_EN', all_labels[4:])
    fields['summary_he'] = grab('SUMMARY_HE', all_labels[5:])
    fields['summary_ar'] = grab('SUMMARY_AR', all_labels[6:])
    fields['translation_en'] = grab('TRANSLATION_EN', all_labels[7:])
    fields['translation_he'] = grab('TRANSLATION_HE', all_labels[8:])
    fields['translation_ar'] = grab('TRANSLATION_AR', [])

    return fields

def multi_input(prompt="Paste your text. Press Enter twice to finish.\n"):
    print(prompt)

    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)

    return "\n".join(lines)

def get_perspective(topic, side):
    """Uses search + Claude to write up a researched perspective (Israeli or
    Palestinian) on a given topic. Used both when uploading (Palestinian
    perspective is requested up front) and when viewing (the other side can
    be generated on demand)."""
    topic = topic[:350]
    query = f"{side} perspective on: {topic}"
    results = search_internet(query)

    prompt = f"""Research and write a factual, well-sourced summary of the {side} perspective
on this topic, based on the search results below. Stay neutral in tone — present
their viewpoint accurately without editorializing. Cite sources found.

Topic: {topic}

Search results:
{results}
"""
    response = client.messages.create(
        model='claude-haiku-4-5-20251001',
        max_tokens=600,
        temperature=0.5,
        system="You are a careful, neutral researcher summarizing a specific community's "
               "perspective on a news topic, grounded only in the provided search results.",
        messages=[{'role': 'user', 'content': prompt}]
    )
    return response.content[0].text


def upload_post_to_supabase(parsed_fields, original_text, palestinian_perspective=None, israeli_perspective=None):
    """Inserts a new row into the 'posts' table in Supabase."""
    data = {
        'original_text': original_text,
        'reliability_score': parsed_fields['reliability_score'],
        'sources': parsed_fields['sources'],
        'verified': parsed_fields['verified'],
        'summary_en': parsed_fields['summary_en'],
        'summary_he': parsed_fields['summary_he'],
        'summary_ar': parsed_fields['summary_ar'],
        'translation_en': parsed_fields['translation_en'],
        'translation_he': parsed_fields['translation_he'],
        'translation_ar': parsed_fields['translation_ar'],
        'palestinian_perspective': palestinian_perspective,
        'israeli_perspective': israeli_perspective,
    }
    result = supabase.table('posts').insert(data).execute()
    return result


def choose_language():
    """Asks the user which language they'd like to view content in."""
    while True:
        choice = input("Which language would you like? (hebrew / arabic / english): ").strip().lower()
        if choice in ('hebrew', 'he', 'עברית'):
            return 'he'
        elif choice in ('arabic', 'ar', 'عربي'):
            return 'ar'
        elif choice in ('english', 'en'):
            return 'en'
        else:
            print("Please type 'hebrew', 'arabic', or 'english'.")


def view_stories():
    """Lists recent posts from Supabase, lets the user pick one, choose a language,
    and optionally view the Israeli/Palestinian perspectives alongside it."""
    result = supabase.table('posts').select('*').order('created_at', desc=True).limit(10).execute()
    posts = result.data

    if not posts:
        print("No stories have been posted yet.")
        return

    print("\nRecent stories:")
    for i, post in enumerate(posts):
        flag = "⚠️ UNVERIFIED" if not post['verified'] else "✅ Verified"
        preview = (post['summary_en'] or post['original_text'] or '')[:80]
        print(f"{i + 1}. [{flag}] {preview}...")

    choice = input("\nWhich story number would you like to view? ").strip()
    try:
        selected = posts[int(choice) - 1]
    except (ValueError, IndexError):
        print("Invalid selection.")
        return

    lang = choose_language()

    if not selected['verified']:
        print("\n⚠️  This story is UNVERIFIED — it could not be confirmed against current "
              "internet sources. It has been published pending review by the Known team.")

    print(f"\n--- Summary ({lang}) ---")
    print(selected[f'summary_{lang}'])
    print(f"\n--- Full Translation ({lang}) ---")
    print(selected[f'translation_{lang}'])
    print(f"\nReliability score: {selected['reliability_score']}/5")
    print(f"Sources: {selected['sources']}")

    show_perspectives = input(
        "\nWould you like to see the Israeli and Palestinian perspectives on this story? (yes/no): "
    ).strip().lower()

    if show_perspectives == 'yes':
        topic = (selected['summary_en'] or selected['original_text'])[:350]

        palestinian = selected.get('palestinian_perspective')
        if not palestinian:
            print("\nGenerating Palestinian perspective (none was submitted with this story)...")
            palestinian = get_perspective(topic, "Palestinian")

        israeli = selected.get('israeli_perspective')
        if not israeli:
            print("Generating Israeli perspective...")
            israeli = get_perspective(topic, "Israeli")

        print(f"\n--- Palestinian Perspective ---\n{palestinian}")
        print(f"\n--- Israeli Perspective ---\n{israeli}")
        print(f"\n--- The Original Submitted Story ---\n{selected['original_text']}")

    delete_choice = input("\nDelete this post? (yes/no): ").strip().lower()
    if delete_choice == 'yes':
        confirm = input("Are you sure? This can't be undone. (yes/no): ").strip().lower()
        if confirm == 'yes':
            supabase.table('posts').delete().eq('id', selected['id']).execute()
            print("Post deleted.")
        else:
            print("Delete cancelled.")


def run_chat():
    print("Welcome to Known! This AI helps you understand more about the region, "
          "your communities, and the nationalities inside of them.")
    print("Type '/view' to browse existing stories, or paste/submit a new story to verify.")
    print("Type 'exit' to quit.\n")

    history = []
    verified_stories = {}
    total_con_tokens = 0
    reply = ""
    turn_number = 0

    while True:
        turn_number += 1
        print(f"[Turn {turn_number}] ", end="")

        if turn_number == 1:
            user_input = multi_input(
                "Paste text, type a PDF filename (e.g., article.pdf), type an image filename "
                "(e.g., photo.jpg), or type '/view' to browse existing stories:\n>> "
            ).strip()
        else:
            user_input = input('>> ').strip()

        if user_input.lower() == '/view':
            view_stories()
            continue

        if user_input.startswith('/get '):
            story_name = user_input.replace('/get ', '')
            if story_name in verified_stories:
                print(verified_stories[story_name])
            else:
                print(f"Sorry, you don't have a story named '{story_name}' in your favorites.")
            continue

        if user_input.lower() == 'exit':
            while True:
                user_rating = input("\nHow would you rate the agent's verification quality? (1-5): ").strip()
                if user_rating in ['1', '2', '3', '4', '5']:
                    print(f"Thank you for your feedback! You rated us: {user_rating}/5")
                    if int(user_rating) < 4:
                        changes = input("Can you tell us what was wrong? ")
                        with open("feedback.txt", "a") as changes_file:
                            changes_file.write(changes + "\n")
                        print("Thank you! we will try to improve next time")
                    break
                else:
                    print("Invalid input. Please enter a number between 1 and 5.")
            break

        if user_input.lower() == 'reset':
            history = []
            turn_number = 0
            print("Conversation history cleared. Starting fresh!")
            continue

        if user_input.lower() == '/summary':
            print("\n--- Known is reviewing your chat history... ---")
            summary_response = client.messages.create(
                model='claude-3-haiku-20240307',
                max_tokens=300,
                temperature=0.9,
                system="You are a helpful tutor. Summarize the user's progress based on the history.",
                messages=history + [{'role': 'user', 'content': 'Please give a quick, structured summary of what we discussed so far and what I learned.'}]
            )
            reply_text = summary_response.content[0].text
            print(f"\n[Summary]: {reply_text}\n")
            continue

        # --- New submission processing (turn 1, or any time a new story is pasted) ---
        original_text_for_storage = user_input
        api_content = None  # will hold either plain text or an image content block

        if user_input.lower().endswith(('.jpg', '.jpeg', '.png')):
            print("Processing your image...\n")
            base64_image = encode_image(user_input)
            media_type = "image/jpeg" if user_input.lower().endswith(('.jpg', '.jpeg')) else "image/png"

            api_content = [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": base64_image}},
                {"type": "text", "text": "Please extract the text/story from this image, verify it against "
                                          "current information, and respond in the required format."}
            ]
            original_text_for_storage = f"[Image submission: {user_input}]"

        elif user_input.lower().endswith('.pdf'):
            print("Processing your PDF...\n")
            story_text = extract_text_from_pdf(user_input)
            search_results = search_internet(story_text)
            api_content = f"User Story:\n{story_text}\n\nInternet Search Results:\n{search_results}"
            original_text_for_storage = story_text

        else:
            print("Processing your story...\n")
            search_results = search_internet(user_input)
            api_content = f"User Story:\n{user_input}\n\nInternet Search Results:\n{search_results}"

        history.append({'role': 'user', 'content': api_content})

        response = client.messages.create(
            model='claude-haiku-4-5-20251001',
            max_tokens=3000,
            temperature=0.7,
            system=system_message,
            messages=history
        )

        reply = response.content[0].text
        print(f'Claude: {reply}')
        history.append({'role': 'assistant', 'content': reply})

        parsed = parse_ai_response(reply)

        if not parsed['verified']:
            print("\n⚠️  This story could NOT be verified against current internet sources. "
                  "It will be published with an unverified warning, pending review by the "
                  "Known team. If it still can't be confirmed, it may be taken down.")

        # Ask for an accompanying Palestinian perspective, as required.
        palestinian_input = input(
            "\nWould you like to submit a certified Palestinian perspective/story to "
            "accompany this post? (paste text, or leave blank to skip): "
        ).strip()

        palestinian_perspective = None
        if palestinian_input:
            print("Researching and writing up this perspective...")
            palestinian_perspective = get_perspective(palestinian_input, "Palestinian")

        upload_post_to_supabase(parsed, original_text_for_storage, palestinian_perspective=palestinian_perspective)
        print("\n✅ Story uploaded. Other perspectives on this topic will be available when viewing it via '/view'.")

        in_tokens = response.usage.input_tokens
        out_tokens = response.usage.output_tokens
        total_tokens = in_tokens + out_tokens
        print(f"[Tokens used — In: {in_tokens} | Out: {out_tokens} | Total: {total_tokens}]")

        total_con_tokens += total_tokens
        print(f"[Running Total: {total_con_tokens} tokens]")

        input_cost = (in_tokens / 1000000) * 0.25
        output_cost = (out_tokens / 1000000) * 1.25
        cost_in_cents = (input_cost + output_cost) * 100
        print(f"[Estimated Cost: {cost_in_cents:.5f}¢]")


if __name__ == "__main__":
    run_chat()