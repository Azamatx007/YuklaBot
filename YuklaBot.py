    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()   # Har doim javob berish kerak

        # Oddiy foydalanuvchi uchun majburiy obuna tekshiruvi
        if query.data == "check_sub":
            if await check_subscription(update, context):
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ Rahmat! Endi botdan foydalanishingiz mumkin.\n\nInstagram linkini yuboring."
                )
            else:
                await query.answer("❌ Siz hali kanalga a'zo emassiz!", show_alert=True)
            return

        # Faqat admin uchun
        if user_id != ADMIN_ID:
            return

        try:
            if query.data == "admin_stat":
                conn = get_db_connection()
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                conn.close()
                await query.message.edit_text(
                    f"📊 <b>Bot foydalanuvchilari:</b> {count} ta",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )

            elif query.data == "toggle_sub":
                conn = get_db_connection()
                conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
                conn.commit()
                conn.close()
                await query.message.edit_text(
                    "🕹 <b>Admin boshqaruv paneli:</b>",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )
                await query.answer("✅ Majburiy obuna holati o'zgartirildi!")

            elif query.data == "edit_channel":
                context.user_data['step'] = 'change_ch'
                await query.message.edit_text(
                    "📝 Yangi kanal yuzernomini yuboring (masalan: @MafiaRoyale2):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Bekor qilish", callback_data="cancel")]])
                )

            elif query.data == "send_help":
                await query.message.edit_text(
                    "🚀 <b>Reklama yuborish uchun:</b>\n\n"
                    "Xabarni (matn, rasm, video) yozing va unga reply qilib <code>/send</code> buyrug'ini bosing.",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )

            elif query.data == "cancel":
                context.user_data['step'] = None
                await query.message.edit_text(
                    "🕹 <b>Admin boshqaruv paneli:</b>",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )

        except BadRequest as e:
            # Bu xato oddiy holat — hech narsa o'zgarmagan
            if "Message is not modified" in str(e):
                await query.answer("✅ Panel yangilandi!")  # Foydalanuvchiga ko'rinadigan javob
            else:
                logger.error(f"Admin panel callback xatosi: {e}")
                await query.answer("❌ Xatolik yuz berdi!", show_alert=True)
        except Exception as e:
            logger.error(f"Admin panel kutilmagan xato: {e}")
            await query.answer("❌ Xatolik yuz berdi!", show_alert=True)
