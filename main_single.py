#!/usr/bin/env python3

import logging
import json
import random
import time
import curses
import curses.panel
import textwrap
import threading
from threading import Timer
from threading import Thread
from collections import Counter


def setup_logging(file_path):
    debug_level = logging.DEBUG
    logging.basicConfig(filename=file_path, level=debug_level,
                        format='%(asctime)s %(levelname)-8s [%(filename)s:%(lineno)d]: %(message)s',
                        datefmt='%Y-%m-%d %H:%M:%S')

def init_terminal():
    curses.start_color()
    curses.use_default_colors()


def init_app_config(file_path):
    try:
        with open(file_path) as f:
            return json.load(f)
    except FileNotFoundError as e:
        logging.critical('Unable to load app config: "{}"\n{}'. \
                         format(file_path, e))
        exit()
    except json.JSONDecodeError as e:
        logging.critical('Unable to process app config: "{}"\n{}'. \
                         format(file_path, e))
        exit()


def init_game_data(file_path):
    try:
        with open(file_path) as f:
            game_json = json.load(f)
            game_config = {
                'display_options': game_json['display_options'],
                'input_options': game_json['input_options'],
                'strings': game_json['strings']
            }
            game_content = game_json['quiz']
    except FileNotFoundError as e:
        logging.critical('Unable to load game file: "{}"\n{}'. \
                         format(file_path, e))
        exit()
    except json.JSONDecodeError as e:
        logging.critical('Unable to process game file: "{}"\n{}'. \
                         format(file_path, e))
        exit()
    except KeyError as e:
        logging.critical('Missing game data key: "{}"\n{}'. \
                         format(file_path, e))
        exit()
    logging.debug('Loaded game: {}'.format(file_path))
    return (game_config, game_content)


def get_color_num_by_name(name) -> int:
    '''
    Return the curses color value associated with a color name.
    '''
    if name:
        try:
            return getattr(curses, 'COLOR_' + name.upper())
        except AttributeError as e:
            logging.critical('Unable to reference color: "{}"\n{}'. \
                             format(name, e))
        else:
            return -1
    else:
        return -1


def get_color_pair(session, color_set_name) -> int:
    return session['colors'][color_set_name]


def set_color_pair(color_index, fg, bg, merge_with=None):
    '''
    Set curses color pair index.
    fg and bg can be color names or curses color numbers.
    blend_with can be passed in to blend a missing fg or bg with
    pre-existing color environment.
    '''
    fg_merged = False
    bg_merged = False
    if merge_with and fg is None:
        fg = merge_with[0]
        fg_merged = True
    if merge_with and bg is None:
        bg = merge_with[1]
        bg_merged = True

    if type(fg) is str or fg is None:
        fg = get_color_num_by_name(fg)
    if type(bg) is str or bg is None:
        bg = get_color_num_by_name(bg)

    # Select the brighter version of the color (except black) if
    # it isn't a merged color since a merged color would already
    # be the correct brightness.
    if 0 < fg < 8 and not fg_merged and curses.COLORS >= 16:
        fg += 8
    if 0 < bg < 8 and not bg_merged and curses.COLORS >= 16:
        bg += 8

    curses.init_pair(color_index, fg, bg)


def create_color_pair(session, window, color_set_name, color_set) -> int:
    '''
    Create a new curses color pair, store its associated name in session.
    Receives color_set dict of fg/bg color names or curses color numbers.

    Return color pair number.
    '''
    i = len(session['colors'])

    # In case color_set does not define fg or bg, send current color set
    # to be merged over the missing values.
    current = curses.pair_content(curses.pair_number(window.getbkgd() & \
                                                     curses.A_COLOR))
    fg = color_set.get('fg', None)
    bg = color_set.get('bg', None)
    set_color_pair(i, fg, bg, merge_with=current)

    session['colors'][color_set_name] = i
    return i


def init_colors(game_config) -> dict:
    '''
    Initialize curses color pairs, associates them with color set names.

    Return a dict of color sets.
    '''
    colors = game_config['display_options'].get('colors', {})
    default_fg = colors.get('default', {}).get('fg', None)
    default_bg = colors.get('default', {}).get('bg', None)

    color_config = {}
    for i, color_set_name in enumerate(colors, 1):
        color_set = colors[color_set_name]
        fg = color_set.get('fg', default_fg)
        bg = color_set.get('bg', default_bg)
        set_color_pair(i, fg, bg)
        color_config[color_set_name] = i
    return color_config


def layout_windows(game_config, main_win):
    '''
    Layout game content areas.

    Return an array of windows.
    '''
    margins = game_config['display_options']['content_window']['margins']
    m_y = margins['y']
    m_x = margins['x']
    main_rows, main_cols = main_win.getmaxyx()
    content_width = main_cols - (2 * m_x)
    windows = {'main': main_win}

    windows['content_title'] = curses.newwin(1, 1, 0, m_x)
    windows['content_body'] = curses.newwin(main_rows - (2 * m_y),
                                            content_width, m_y, m_x)
    windows['prompt'] = curses.newwin(1, content_width, main_rows - 2, m_x)
    windows['status'] = curses.newwin(1, content_width, main_rows - 2, m_x)

    return windows


def init_main_window(window):
    window.clear()
    window.border()
    window.refresh()


def init_session(game_content) -> dict:
    session = {}
    # Session needs to be reset between each game play
    randomize = game_content['randomize']
    # Each session gets a subset of all available q&a
    # Make a copy in order to keep original data intact
    qa_set = game_content['questions_answers'][:]
    if(randomize):
        random.shuffle(qa_set)
    qa_set = qa_set[:game_content['questions_per_session']]
    if(randomize):
        for qa in qa_set:
            random.shuffle(qa['answers'])
    session['questions_answers'] = qa_set
    session['default_result'] = game_content['default_result']
    #session['game_type'] = game_content.get('game_type', 'default')
    session['user_responses'] = {}
    return session


def reset_window(window):
    window.clear()
    window.refresh()


def center_text(text, mode='block', width=None, height=None) -> str:
    '''
    Center text on width and height.
    In 'block' mode, centers the entire body of text as a whole and is
        dependent on the longest line..
    In 'line' mode (or not 'block'), each line is centered dependent on
        its individual length.
    Works for single lines as well as multiple.

    Return centered string.
    '''
    split_text = text.splitlines(keepends=True)
    centered_text = ''

    if width is not None and type(width) is int:
        if mode == 'block':
            longest_length = 0
            for line in split_text:
                line_length = len(line)
                if line_length > longest_length:
                    longest_length = line_length
            padding_size = (width - longest_length) // 2 
            if padding_size > 0:
                padding = padding_size * ' '
                for line in split_text:
                    centered_text += padding + line
            elif padding_size < 0:
                for line in split_text:
                    centered_text += line[-padding_size:]
            else:
                centered_text = text
        else:  # 'line'
            for line in split_text:
                padding_size = (width - len(line)) // 2 
                if padding_size > 0:
                    centered_text += (' ' * padding_size) + line
                elif padding_size < 0:
                    centered_text += line[-padding_size:]
                else:
                    centered_text = line
    else:
        centered_text = text

    if height is not None and type(height) is int and height > len(split_text):
        centered_text = (((height - len(split_text)) // 2) * '\n') + \
                        centered_text
    return centered_text


def wrap_text(text, width, first_line_offset=0,
              preserve_whitespace=False) -> str:
    '''
    Wrap a body of text.
    As per the documentation recommendation, each paragraph is wrapped
    separately in order to retain paragraph breaks.

    Return wrapped text as a single string.
    '''
    text = (' ' * first_line_offset) + text
    wrapped_text = ''
    for line in text.splitlines(keepends=True):
        lines = textwrap.wrap(line, width=width,
                              drop_whitespace=not preserve_whitespace)
        wrapped_text += '\n'.join(lines)
        if line[-1] == '\n':
            wrapped_text += '\n'
    return wrapped_text[first_line_offset:]


def indent_text(text, width, first_line_dedent=True) -> str:
    '''
    Indent a body of text. Dedent first line when, for example, 
    it starts on an existing, partial line.
    '''
    indented_text = textwrap.indent(text, ' ' * width)
    if first_line_dedent:
        indented_text = indented_text[width:]
    return indented_text


def teletype(text, window, fps=30, mode='chr', attr=None,
             interruptable=False) -> bool:
    '''
    Display one character or line at a time in sequence.

    Return if interrupted by keypress or not.
    '''

    # Seconds per frame
    spf = 1 / fps 

    if mode == 'chr':
        iter = text
    else:  # 'line'
        iter = text.splitlines(keepends=True)

    if attr is None:
        attr = window.getbkgd()

    if interruptable:
        window.nodelay(1)
    interrupted = False

    max_row, max_col = window.getmaxyx()

    for i, s in enumerate(iter):
        cur_row, cur_col = window.getyx()
        if (cur_row == max_row - 1) and (cur_col == max_col - 1):
            window.insstr(s, attr)
        else:
            window.addstr(s, attr)
        if interruptable and (window.getch() != -1):
            interrupted = True
            break
        window.refresh()
        time.sleep(spf)

    if interrupted:
        if mode == 'chr':
            window.addstr(text[i + 1:])
        else:
            window.addstr(''.join(iter[i + 1:]))
        window.refresh()

    window.nodelay(0)

    return interrupted


def set_cursor(style, window):
    '''
    Set custom cursor style.

    If animated, return animation Thread.
    '''
    animate_cursor_flag = False
    if style is 'none':
        # Invisible cursor
        curses.curs_set(0)
    elif style is not 'default':
        curses.curs_set(0)
        cursor_col = window.getyx()[1]
        if style == 'waiting':
            # Blinking block
            cursor_chars = [curses.ACS_BLOCK, curses.ACS_BLOCK,
                            curses.ACS_BLOCK, curses.ACS_BLOCK,
                            ' ', ' ', ' ']
        elif style == 'input':
            # Blinking underline
            cursor_chars = [curses.ACS_S9, curses.ACS_S9,
                            curses.ACS_S9, curses.ACS_S9,
                            ' ', ' ', ' ']


        def animate_cursor(row, cursor_col, cursor_chars):
            '''
            Cursor animation loop
            '''
            cursor_char_i = 0
            while animate_cursor_flag:
                window.addch(cursor_chars[cursor_char_i])
                window.move(row, cursor_col)
                cursor_char_i = (cursor_char_i + 1) % len(cursor_chars)
                window.refresh()
                time.sleep(0.15)


        animate_cursor_flag = True
        cursor_t = Thread(target=animate_cursor,
                          args=(row, cursor_col, cursor_chars),
                          daemon=True)
        cursor_t.start()
        return cursor_t
    else:
        # Default
        curses.curs_set(1)

    return cursor_t
 

def await_prompted_anykey(text, window):
    curses.flushinp()
    curses.curs_set(1)

    # Set prompt text
    text = text[:window.getmaxyx()[1] - 2]
    window.addstr(text)
    window.refresh()

    # Wait until input
    window.timeout(-1)
    window.getch()

    window.clear()
    window.refresh()
    curses.curs_set(1)


def await_timed_key(wait_time, window):
    '''
    Wait for wait_time seconds for keypress.

    Return keypress character.
    '''
    curses.flushinp()
    curses.curs_set(1)
    window.timeout(int(wait_time * 1000))  # Expects milliseconds
    ch = window.getch()
    curses.curs_set(0)
    return ch


def fade_in(text, window, duration=2, attr=None, interruptable=False) -> bool:
    '''
    Fade in a body of text by randomly filling in one character
    at a time.
    '''
    if interruptable:
        window.nodelay(1)
    interrupted = False

    if attr is None:
        attr = window.getbkgd()

    # Store visible characters in a dict keyed to screen coordinates
    max_row, max_col = window.getmaxyx()
    chars = {}
    for y, line in enumerate(text.splitlines()):
        for x, ch in enumerate(line):
            if (ch not in [' ', '']) and \
               (0 <= y < max_row) and \
               (0 <= x < max_col):
                chars[(y, x)] = ch

    # Set up to draw characters in random order
    coords = list(chars.keys())
    random.shuffle(coords)

    # Animation duration remains constant regardless of number
    # of characters to draw
    spf = duration / (5 * len(coords))

    # Draw characters
    curses.curs_set(0)
    for coord in coords:
        window.addstr(*coord, chars[coord], attr)
        if interruptable and (window.getch() != -1):
            interrupted = True
            break
        window.refresh()
        time.sleep(spf)

    if interrupted:
        for coord in coords:
            window.addstr(*coord, chars[coord], attr)
        window.refresh()

    curses.curs_set(1)
    window.nodelay(0)

    return interrupted


def display_title(file_path, game_config, window):
    '''
    Display title screen
    '''
    try:
        with open(file_path) as f:
            text = f.read()
    except FileNotFoundError as e:
        logging.critical('Unable to load game title file: "{}"\n{}'. \
                         format(file_path, e))
        exit()

    animation_options = game_config['display_options']['animation']
    curses.curs_set(0)  # Turn off cursor visibility
    max_row, max_col = window.getmaxyx()
    text = center_text(text, height=max_row, width=max_col)
    animation_style = animation_options.get('title', 'fade-in')
    if animation_style == 'fade-in':
        duration = animation_options.get('fade_duration', 2)
        fade_in(text, window, duration=duration, interruptable=True)
    else:  # 'line'
        fps = animation_options.get('fps', 60)
        teletype(text, window, fps=fps, mode='line', interruptable=True)
    curses.curs_set(1)  # Turn on cursor visibility


def display_intro(file_path, game_config, window):
    '''
    Display introduction screen
    '''
    options = game_config['display_options']
    try:
        with open(file_path) as f:
            wrap_width = options['text_wrap_width']
            text = wrap_text(f.read(), wrap_width)
    except FileNotFoundError as e:
        logging.critical('Unable to load game intro: "{}"\n{}'. \
                         format(file_path, e))
        exit()

    curses.curs_set(1)  # Turn on cursor visibility
    fps = options['animation']['fps']
    teletype(text, window, fps=fps, interruptable=True)
    curses.curs_set(0)  # Turn off cursor visibility


def display_status(text, status_win, main_win, attr=None, fps=None):
    # Remember cursor position in the calling window
    prev_pos = main_win.getyx()

    max_col = status_win.getmaxyx()[1]
    status_win.clear()
    if fps:
        teletype(text, status_win, fps=fps, attr=attr)
    else:
        status_win.addstr(text[:max_col-1], attr)
        status_win.refresh()

    # Restore cursor location in calling window
    main_win.move(*prev_pos)
    main_win.refresh()


def all_max(arg) -> list:
    '''
    Return a list of all equally maximum values rather than just the first.

    Arguments:
    arg: list or dict
    '''
    arg_type = type(arg)
    if arg_type is list:
        return []
    elif arg_type is dict:
        return []
    else:
        return [False]
    pass


def calc_result(responses):
    '''
    Return winner if simple majority, else return None.
    '''
    count = Counter(responses)
    highest_count = 0
    all_equal = len(count) != 1
    prev_v = 0
    for k, v in count.items():
        if v > highest_count:
            highest_count = v
            winner = k
        if all_equal and (prev_v > 0) and (v != prev_v):
            all_equal = False
        prev_v = v
    if not all_equal:
        return winner
    else:
        return None


def display_results(game_config, session, windows):
    strings = game_config['strings']
    options = game_config['display_options']
    fps = options['animation']['fps']
    fps_slow = options['animation']['fps_slow']
    drumroll_prepost_pause = options['drumroll_prepost_pause']
    drumroll_pause = options['drumroll_pause']
    result_pause = options['result_pause']
    width = options['text_wrap_width']
    hline = strings['line_separator']

    result = calc_result(session['user_responses']['responses'])
    if result == None:
        result = session['default_result']
    curses.curs_set(1)

    win = windows['content_body']
    prompt_win = windows['prompt']

    # Result drumroll
    reset_window(win)
    text = strings['result_drumroll_header']
    teletype(text, win, fps)
    time.sleep(drumroll_prepost_pause)
    win.addstr('\n\n')
    cur_row, cur_col = win.getyx()
    drumroll = []
    for words in strings['result_drumroll']:
        drumroll.append(words)
    num_drumroll = min([len(drumroll[0]),
                        len(drumroll[1]),
                        len(drumroll[2])])
    num_drumroll = 3 if 3 <= num_drumroll else num_drumroll
    for i, words in enumerate(drumroll):
        random.shuffle(words)
        drumroll[i] = words[:num_drumroll]
    for drumroll_option in zip(drumroll[0], drumroll[1], drumroll[2]):
        text = ' '.join(drumroll_option) + ('.' * 5)
        teletype(text, win, fps)
        time.sleep(drumroll_pause)

        # Display only one drumroll message at a time
        win.move(cur_row, cur_col)
        win.clrtobot()
    reset_window(win)
    text = strings['result_drumroll_footer']
    teletype(text, win, fps)
    time.sleep(drumroll_prepost_pause)
    reset_window(win)

    # Result

    #current_colors = win.getbkgd() & curses.A_COLOR  # current bkgd color attr
    #win.addstr(2, 0, str(curses.pair_number(current_colors)))
    #win.refresh()
    #time.sleep(10)

    result_data = strings['result_message'][result]

    # Get result hilite color
    color_set = result_data.get('color', {})
    result_attr = curses.color_pair(create_color_pair(session, win,
                                                      result, color_set))
    text = wrap_text(strings['result_message'][result]['pre'],
                     width, preserve_whitespace=True)
    teletype(text, win, fps_slow)
    text = wrap_text(result, width, first_line_offset=win.getyx()[1])
    teletype(text, win, fps_slow, attr=result_attr)
    text = wrap_text('{}\n\n'. \
                     format(strings['result_message'][result]['post']),
                     width, first_line_offset=win.getyx()[1])
    teletype(text, win, fps_slow)
    time.sleep(result_pause)

    # Story conclusion
    text = strings['result_message'][result]['story']
    if text != '':
        text = wrap_text('{}\n\n'.format(text), width)
        teletype(text, win, fps)
        time.sleep(1)

    # End message
    text = wrap_text('{}\n\n{}'.format(hline * width,
                     strings['common_end_message']), width)
    teletype(text, win, fps, attr=curses.A_DIM)

    logging.info('Responses: {}\nTotals {}'.format(
        str(session['user_responses']['responses']),
        str(Counter(session['user_responses']['responses']))
    ))

    curses.curs_set(0)


def play(game_config, game_content, session, windows) -> bool:
    '''
    Main game body.
    Displays rounds of question/answers, waits for user responses,
    stores responses.

    Return True if play completes normally. False if timed out.
    '''
    options = game_config['display_options']
    fps = options['animation']['fps']
    fps_fast = options['animation']['fps_fast']
    width = options.get('text_wrap_width', 55)
    strings = game_config['strings']
    q_label_base = strings['question_label']
    a_label_base = strings['answer_label']
    separator = strings['line_separator']

    body_win = windows['content_body']
    title_win = windows['content_title']
    prompt_win = windows['prompt']
    status_win = windows['status']

    # Resetting the 'main' window from here removes the screen border
    # so only reset other windows
    win_list = []
    for k in windows.keys():
        if k != 'main':
            win_list.append(windows[k])

    warn_attr = curses.color_pair(get_color_pair(session, "warning"))
    err_attr = curses.color_pair(get_color_pair(session, "error"))

    responses = []
    max_row, max_col = body_win.getmaxyx()
    cur_row, cur_col = body_win.getyx()

    for q_num, qa in enumerate(session['questions_answers']):
        #for w in win_list:
        #    reset_window(w)
        reset_window(title_win)
        reset_window(body_win)

        curses.curs_set(1)

        # Question/answers
        # Window title
        text = '[ {} of {} ]'.format(chr(ord(q_label_base) + q_num),
                                     chr(ord(q_label_base) +
                                         len(session['questions_answers'])
                                         - 1))
        title_win.resize(title_win.getmaxyx()[0], len(text))
        teletype(text, title_win, fps, attr=curses.A_DIM)

	# Calculate length of hline from window title
        #hline = separator * len(text.strip())

	# Question
        text = wrap_text('{}\n\n'.format(qa['question']), width)
        teletype(text, body_win, fps, attr=curses.A_BOLD)

        # Answers
        allowed_responses = []
        for a_num, a in enumerate(qa['answers']):
            # Answer labels are displayed and also stored to compare
            # to user responses for validity
            a_label = chr(ord(a_label_base) + a_num)
            allowed_responses.append(a_label.lower())

            teletype(a_label, body_win, fps, attr=curses.A_BOLD)
            teletype(' - ', body_win, fps, attr=curses.A_DIM)

            col = body_win.getyx()[1]
            text = indent_text(wrap_text(a['text'], width - col), col)
            #teletype(text, body_win, fps, attr=curses.A_DIM)
            teletype(text, body_win, fps)

            '''
            # If cheat codes are on, show answer categories
            #if(logging.getLogger().getEffectiveLevel() >= logging.DEBUG):
            if cheat_mode:
                text = ' ({})'.format(a['category'])
                teletype(text, body_win, fps, attr=curses.A_DIM)
            '''
            text = '\n\n'
            teletype(text, body_win, fps)

        # Input prompt
        text = '{}{}'.format(strings['input_prompt'], strings['prompt_mark'])
        teletype(text, body_win, fps, attr=curses.A_BOLD)
        curses.curs_set(0)

        # Input time warning
        def get_warning_timer():
            return Timer(warning_time, display_status,
                         args=[warn_text, status_win, body_win],
                         kwargs={"attr":warn_attr, "fps":fps_fast})


        warning_time = game_config['input_options']. \
                    get('reset_warning_time', None)
        warn_text = strings.get('timeout_warning', '')

        # Wait for input
        while True:
            warning_timer = get_warning_timer()
            warning_timer.start()

            ch = await_timed_key(game_config['input_options']
                                            ['reset_time'], body_win)

            warning_timer.cancel()

            # Timed out without response
            if ch == -1:
                curses.beep()
                display_status(strings['err_no_input'], status_win,
                               body_win, attr=err_attr, fps=fps_fast)
                time.sleep(2)
                return False

            # Validate and record input 
            c = chr(ch)
            if c.lower() in allowed_responses:
                a_index = ord(c.upper()) - ord(a_label_base.upper())
                a_category = qa['answers'][a_index]['category']

                # Store response for tabulation
                responses.append(a_category)

                # Display response
                body_win.addstr(c.upper())
                body_win.refresh()
                time.sleep(game_config['display_options']
                                      ['input_reflect_pause'])
                break
            else:
                # Invalid input
                display_status(strings['err_invalid_input'], status_win,
                               body_win, attr=warn_attr, fps=fps_fast)
        try:
            warning_timer.cancel()
        except:
            pass
    session['user_responses']['responses'] = responses
    return True


def setup_game_menu_container(app_config, window):
    '''
    Set up empty game library menu with border and heading.

    Return inner window that will hold the game list.
    '''
    heading = app_config['strings']['menu_heading']
    max_row, max_col = window.getmaxyx()
    height = 18 if 18 < max_row else max_row
    width = 50 if 50 < max_col else max_col
    padding_x = 2

    # Animate window opening sequence
    curses.curs_set(0)
    box = curses.newwin(1, width, (max_row - 1) // 2, (max_col - width) // 2)
    box_origin_x = box.getbegyx()[1]
    for h in range(1, height + 1, height // 5):
        box.mvwin((max_row - h) // 2, box_origin_x)
        box.resize(h, width)
        box.clear()
        box.border()
        box.refresh()
        time.sleep(0.1)
    box.mvwin((max_row - height) // 2, box_origin_x)
    box.resize(height, width)
    box.clear()
    box.border()

    # Heading
    box.addstr(1, 1, center_text(heading, width=width - 2))
    box.addch(box.getyx()[0] + 1, 0, curses.ACS_LTEE)
    box.hline(curses.ACS_HLINE, width - 2)
    box.addch(box.getyx()[0], width - 1, curses.ACS_RTEE)

    box.refresh()
    curses.curs_set(1)

    # Return inner window
    return box.derwin(height - 4, width - 2 - 2 * padding_x, 1 + padding_x, 3)


def populate_game_menu(app_config, window) -> list:
    '''
    Populate game library menu.

    Return a list of allowable keyboard inputs for selecting menu items.
    '''
    max_row, max_col = window.getmaxyx()
    allowed_responses = []
    for i, game in enumerate(app_config['game_library']):
        # Generate label and associated keyboard input
        label = chr(ord('A') + i)
        allowed_responses.append(label.lower())
        label += ') '
        name = game['name']
        lines = textwrap.wrap(name, max_col - len(label) - 1)
        name = '\n'.join(lines)

        # Indent to width of label and add label to first line
        name = textwrap.indent(name, ' ' * len(label))
        name = label + name[len(label):]

        window.addstr('\n{}\n'.format(name))

    # Input prompt
    window.addstr(max_row - 1, 0, app_config['strings']['menu_prompt'])

    window.refresh()
    return allowed_responses


def get_game_choice(window, allowed_responses) -> int:
    curses.curs_set(1)
    window.timeout(-1)
    ch = 0
    while chr(ch).lower() not in allowed_responses:
        ch = window.getch()
    curses.curs_set(0)

    # Display response
    window.addstr(chr(ch).upper())
    window.refresh()

    # Return normalized ASCII code of response
    return ord(chr(ch).lower()) - ord('a')


def main(main_win):
    '''
    Handle loading configuration and data, launch game from a menu,
    set up the game session, launch game play.
    Receives the main curses window as main_win.

    app_config
        do not modify
        once game library choice is made, load game data
    game_config
        do not modify
        display options, file locations, etc
    game_content
        do not modify
        strings, q&a, etc
    session
        current game session content (derived from game_content),
            custom colors
        user responses

    TODO: Receive config dir/file as CLI arguments
    '''
    logging.debug('---------- Session started ----------')

    data_dir = './data/'
    app_config = init_app_config(data_dir + 'config.json')
    file_names = app_config['common_file_names']

    init_terminal()

    # Display menu of games, get game choice
    menu_win = setup_game_menu_container(app_config, main_win)
    allowed_responses = populate_game_menu(app_config, menu_win)
    game_choice = get_game_choice(menu_win, allowed_responses)

    # Load game config and content based on the library selection
    game_dir = data_dir + app_config['game_library'][game_choice]['dir']
    game_config, game_content = init_game_data(game_dir + file_names['game'])

    # Pause after getting game choice input
    time.sleep(game_config['display_options']['input_reflect_pause'])

    # Layout content areas
    windows = layout_windows(game_config, main_win)

    # Load game-specific colors
    color_config = init_colors(game_config)

    # Prompt verbiage
    continue_text = center_text(game_config['strings']['continue_prompt'],
                                width=windows['prompt'].getmaxyx()[1])
    restart_text = center_text(game_config['strings']['restart_prompt'],
                               width=windows['prompt'].getmaxyx()[1])

    # Title screen, intro screen, play, result loop
    while True:
        # Initialize session
        session = init_session(game_content)
        session['colors'] = color_config

        # Clear windows, set colors
        for w in windows.values():
            w.bkgd(' ', curses.color_pair(get_color_pair(session, "default")))
            reset_window(w)

        display_title(game_dir + file_names['title'], game_config, main_win)
        await_prompted_anykey(continue_text, windows['prompt'])
        reset_window(main_win)

        display_intro(game_dir + file_names['intro'], game_config,
                      windows['content_body'])
        await_prompted_anykey(continue_text, windows['prompt'])
        reset_window(windows['content_body'])

        # Start gameplay
        init_main_window(main_win)

        if play(game_config, game_content, session, windows):
            # Game completed successfully

            for w in windows.values():
                reset_window(w)
            # Redraw the void left behind by empty content title window
            init_main_window(main_win)

            display_results(game_config, session, windows)
            await_prompted_anykey(restart_text, windows['prompt'])


if __name__ == '__main__':
    setup_logging('main.log')
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        if threading.active_count() > 1:
            for t in threading.enumerate()[1:]:
                try:
                    # For active Timers
                    t.cancel()
                except:
                    pass
                else:
                    # For all other active Threads
                    t.join()
        print('\nYou escaped!\n')
    except SystemExit:
        print('\nPossible crash. Check log for details.\n')
